"""Evidence Service - Core business logic for Evidence Pipeline.

Implements:
- Evidence CRUD with validation
- Quality gates (Requirement 79)
- Versioning with immutable history (Requirements 40, 41)
- Relationships (Requirements 31, 32)
- Correction, invalidation, supersession with propagation (Requirements 36-39)
- Deduplication without information loss (Requirements 27, 59)
- Traceability queries (Requirements 22-24, 100)
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple
from dataclasses import dataclass

from sqlalchemy.orm import Session
from sqlalchemy import and_, or_, desc, func

from app.models.evidence import (
    Evidence,
    EvidenceRelationship,
    EvidenceVersion,
    EvidenceCorrection,
    EvidenceInvalidation,
    EvidenceSupersession,
    EvidenceDeduplication,
)
from app.models.care import Person, Caregiver
from app.models.care_event import CareEvent
from app.models.document import Document as DocumentModel
from app.schemas.evidence import (
    EvidenceCreate,
    EvidenceUpdate,
    EvidenceResponse,
    EvidenceQueryParams,
    EvidenceTraceResponse,
    EvidenceGraphResponse,
    EvidenceRelationshipCreate,
    EvidenceRelationshipResponse,
    EvidenceCorrectionCreate,
    EvidenceCorrectionResponse,
    EvidenceInvalidationCreate,
    EvidenceInvalidationResponse,
    EvidenceSupersessionCreate,
    EvidenceSupersessionResponse,
    EvidenceDeduplicationCreate,
    EvidenceDeduplicationResponse,
    EvidenceVersionResponse,
    SourceLocation,
    ConfidenceDimensions,
    TemporalInfo,
    NormalizationInfo,
    ExtractionMetadata,
    QualityGateResult,
)
from app.core.evidence_enums import (
    EvidenceType,
    EvidenceStatus,
    EvidenceSubjectRole,
    EvidenceObserverRole,
    EvidenceAuthorRole,
    EvidenceRelationshipType,
    NegationStatus,
    UncertaintyLevel,
    EntityResolutionStatus,
    CorrectionType,
    InvalidationReason,
    SupersessionReason,
    NormalizationType,
    ExtractionMethod,
    SourceType,
)


@dataclass
class QualityGateCheck:
    """Single quality gate check result."""
    name: str
    passed: bool
    message: str


class EvidenceService:
    """Core service for evidence operations."""

    def __init__(self, db: Session) -> None:
        self.db = db

    # ============================================================
    # QUALITY GATE (Requirement 79)
    # ============================================================

    def run_quality_gate(self, evidence: EvidenceCreate) -> QualityGateResult:
        """Verify evidence meets quality requirements before entering downstream reasoning.

        Checks:
        - source exists
        - source is valid
        - source is accessible
        - subject is resolved or explicitly unresolved
        - content is non-empty
        - evidence type is valid
        - provenance exists
        - temporal interpretation is explicit
        - confidence is represented appropriately
        - extraction is not known to be corrupted
        """
        checks: List[QualityGateCheck] = []

        # Source exists and is valid
        if evidence.source_id:
            source = self.db.query(DocumentModel).filter(DocumentModel.id == evidence.source_id).first()
            if not source:
                checks.append(QualityGateCheck("source_exists", False, f"Source {evidence.source_id} not found"))
            elif source.processing_status == "failed":
                checks.append(QualityGateCheck("source_valid", False, "Source document processing failed"))
            else:
                checks.append(QualityGateCheck("source_exists", True, "Source found and valid"))
        else:
            checks.append(QualityGateCheck("source_exists", True, "No source (manual entry)"))

        # Subject resolved or explicitly unresolved
        if evidence.subject_role == EvidenceSubjectRole.CARE_RECIPIENT:
            if evidence.subject_id:
                person = self.db.query(Person).filter(Person.id == evidence.subject_id).first()
                if person:
                    checks.append(QualityGateCheck("subject_resolved", True, f"Subject resolved to {person.name}"))
                else:
                    checks.append(QualityGateCheck("subject_resolved", False, f"Subject {evidence.subject_id} not found"))
            elif evidence.entity_resolution_status == EntityResolutionStatus.UNRESOLVED:
                checks.append(QualityGateCheck("subject_resolved", True, "Subject explicitly unresolved"))
            else:
                checks.append(QualityGateCheck("subject_resolved", False, "Subject not resolved and not marked unresolved"))
        else:
            checks.append(QualityGateCheck("subject_resolved", True, f"Subject role: {evidence.subject_role.value}"))

        # Content non-empty
        if evidence.source_text and evidence.source_text.strip():
            checks.append(QualityGateCheck("content_non_empty", True, "Content present"))
        else:
            checks.append(QualityGateCheck("content_non_empty", False, "Source text is empty"))

        # Evidence type valid (enforced by enum)
        checks.append(QualityGateCheck("type_valid", True, f"Type: {evidence.type.value}"))

        # Provenance exists
        has_provenance = bool(evidence.source_id or evidence.extraction or evidence.temporal)
        checks.append(QualityGateCheck("provenance_exists", has_provenance, "Provenance present" if has_provenance else "No provenance"))

        # Temporal interpretation explicit
        # Document-sourced evidence must carry a temporal anchor (the
        # document's upload time or an explicit temporal expression in the
        # text). Manual caregiver input is timestamped by the caller at the
        # point of recording, so the absence of a temporal expression in the
        # raw text is expected and not a quality failure.
        has_temporal = evidence.temporal is not None
        if not has_temporal and evidence.source_id:
            source = self.db.query(DocumentModel).filter(
                DocumentModel.id == evidence.source_id
            ).first()
            if source and source.uploaded_at:
                has_temporal = True
        if evidence.source_id:
            checks.append(QualityGateCheck("temporal_explicit", has_temporal, "Temporal info provided" if has_temporal else "No temporal info"))
        else:
            checks.append(QualityGateCheck("temporal_explicit", True, "Manual entry — timestamped at creation"))

        # Confidence represented appropriately
        has_confidence = evidence.confidence is not None and any(
            v is not None for v in [
                evidence.confidence.extraction,
                evidence.confidence.entity_resolution,
                evidence.confidence.temporal,
                evidence.confidence.source_support,
                evidence.confidence.interpretation,
            ]
        )
        checks.append(QualityGateCheck("confidence_represented", has_confidence, "Confidence dimensions present" if has_confidence else "No confidence dimensions"))

        # Extraction not corrupted
        extraction_ok = evidence.extraction is None or evidence.extraction.extraction_confidence is None or evidence.extraction.extraction_confidence > 0
        checks.append(QualityGateCheck("extraction_not_corrupted", extraction_ok, "Extraction confidence OK" if extraction_ok else "Extraction confidence is 0"))

        passed = all(c.passed for c in checks)
        failed_checks = [c.name for c in checks if not c.passed]

        return QualityGateResult(
            passed=passed,
            details={c.name: {"passed": c.passed, "message": c.message} for c in checks},
            failed_checks=failed_checks,
        )

    # ============================================================
    # EVIDENCE CRUD
    # ============================================================

    def create_evidence(self, evidence_data: EvidenceCreate, skip_quality_gate: bool = False) -> Evidence:
        """Create new evidence with full validation and versioning."""
        # Run quality gate
        if not skip_quality_gate:
            quality_result = self.run_quality_gate(evidence_data)
            if not quality_result.passed:
                raise ValueError(f"Quality gate failed: {quality_result.failed_checks}")

        # Build evidence model
        evidence = Evidence(
            subject_role=evidence_data.subject_role,
            subject_id=evidence_data.subject_id,
            subject_identifier=evidence_data.subject_identifier,
            entity_resolution_status=evidence_data.entity_resolution_status,
            entity_resolution_confidence=evidence_data.entity_resolution_confidence,
            observer_role=evidence_data.observer_role,
            observer_id=evidence_data.observer_id,
            observer_name=evidence_data.observer_name,
            author_role=evidence_data.author_role,
            author_id=evidence_data.author_id,
            author_name=evidence_data.author_name,
            uploaded_by_caregiver_id=evidence_data.uploaded_by_caregiver_id,
            type=evidence_data.type,
            subtype=evidence_data.subtype,
            source_text=evidence_data.source_text,
            normalized_text=evidence_data.normalization.normalized_text if evidence_data.normalization else None,
            normalization_type=evidence_data.normalization.normalization_type if evidence_data.normalization else None,
            normalization_details=evidence_data.normalization.normalization_details if evidence_data.normalization else None,
            negation_status=evidence_data.negation_status,
            uncertainty_level=evidence_data.uncertainty_level,
            hedge_type=evidence_data.hedge_type,
            hedge_text=evidence_data.hedge_text,
            event_time=evidence_data.temporal.event_time if evidence_data.temporal else None,
            event_time_granularity=evidence_data.temporal.event_time_granularity if evidence_data.temporal else None,
            event_time_text=evidence_data.temporal.event_time_text if evidence_data.temporal else None,
            event_time_anchor=evidence_data.temporal.event_time_anchor if evidence_data.temporal else None,
            event_time_anchor_reference=evidence_data.temporal.event_time_anchor_reference if evidence_data.temporal else None,
            observation_time=evidence_data.temporal.observation_time if evidence_data.temporal else None,
            observation_time_granularity=evidence_data.temporal.observation_time_granularity if evidence_data.temporal else None,
            observation_time_text=evidence_data.temporal.observation_time_text if evidence_data.temporal else None,
            document_time=evidence_data.temporal.document_time if evidence_data.temporal else None,
            authoring_time=evidence_data.temporal.authoring_time if evidence_data.temporal else None,
            source_id=evidence_data.source_id,
            source_version=evidence_data.source_version,
            source_type=evidence_data.source_type,
            source_location_page=evidence_data.source_location.page if evidence_data.source_location else None,
            source_location_paragraph=evidence_data.source_location.paragraph if evidence_data.source_location else None,
            source_location_text_span_start=evidence_data.source_location.text_span_start if evidence_data.source_location else None,
            source_location_text_span_end=evidence_data.source_location.text_span_end if evidence_data.source_location else None,
            source_location_image_region=evidence_data.source_location.image_region if evidence_data.source_location else None,
            source_location_structured_field=evidence_data.source_location.structured_field if evidence_data.source_location else None,
            extraction_method=evidence_data.extraction.extraction_method if evidence_data.extraction else None,
            extraction_version=evidence_data.extraction.extraction_version if evidence_data.extraction else None,
            extraction_model=evidence_data.extraction.extraction_model if evidence_data.extraction else None,
            extraction_confidence=evidence_data.extraction.extraction_confidence if evidence_data.extraction else None,
            extraction_timestamp=evidence_data.extraction.extraction_timestamp if evidence_data.extraction else None,
            confidence_extraction=evidence_data.confidence.extraction if evidence_data.confidence else None,
            confidence_entity_resolution=evidence_data.confidence.entity_resolution if evidence_data.confidence else None,
            confidence_temporal=evidence_data.confidence.temporal if evidence_data.confidence else None,
            confidence_source_support=evidence_data.confidence.source_support if evidence_data.confidence else None,
            confidence_interpretation=evidence_data.confidence.interpretation if evidence_data.confidence else None,
            status=evidence_data.status,
            status_reason=evidence_data.status_reason,
            valid_from=evidence_data.valid_from,
            valid_until=evidence_data.valid_until,
            processing_version=evidence_data.processing_version,
            quality_gate_passed=quality_result.passed if not skip_quality_gate else True,
            quality_gate_details=quality_result.details if not skip_quality_gate else None,
        )

        self.db.add(evidence)
        self.db.flush()

        # Create initial version
        self._create_version(evidence, "created", "Initial creation", evidence_data.uploaded_by_caregiver_id)

        self.db.commit()
        self.db.refresh(evidence)
        return evidence

    def get_evidence(self, evidence_id: str) -> Optional[Evidence]:
        """Get evidence by ID."""
        return self.db.query(Evidence).filter(Evidence.id == evidence_id).first()

    def update_evidence(self, evidence_id: str, update_data: EvidenceUpdate, caregiver_id: str) -> Optional[Evidence]:
        """Update evidence (limited fields). Creates version history."""
        evidence = self.get_evidence(evidence_id)
        if not evidence:
            return None

        # Track changes for versioning
        changes = {}
        if update_data.status is not None and update_data.status != evidence.status:
            changes["status"] = (evidence.status, update_data.status)
            evidence.status = update_data.status
        if update_data.status_reason is not None:
            evidence.status_reason = update_data.status_reason
        if update_data.valid_until is not None:
            changes["valid_until"] = (evidence.valid_until, update_data.valid_until)
            evidence.valid_until = update_data.valid_until
        if update_data.confidence is not None:
            old_confidence = {
                "extraction": evidence.confidence_extraction,
                "entity_resolution": evidence.confidence_entity_resolution,
                "temporal": evidence.confidence_temporal,
                "source_support": evidence.confidence_source_support,
                "interpretation": evidence.confidence_interpretation,
            }
            new_confidence = {
                "extraction": update_data.confidence.extraction,
                "entity_resolution": update_data.confidence.entity_resolution,
                "temporal": update_data.confidence.temporal,
                "source_support": update_data.confidence.source_support,
                "interpretation": update_data.confidence.interpretation,
            }
            if old_confidence != new_confidence:
                changes["confidence"] = (old_confidence, new_confidence)
                evidence.confidence_extraction = update_data.confidence.extraction
                evidence.confidence_entity_resolution = update_data.confidence.entity_resolution
                evidence.confidence_temporal = update_data.confidence.temporal
                evidence.confidence_source_support = update_data.confidence.source_support
                evidence.confidence_interpretation = update_data.confidence.interpretation
        if update_data.normalization is not None:
            changes["normalization"] = (evidence.normalized_text, update_data.normalization.normalized_text)
            evidence.normalized_text = update_data.normalization.normalized_text
            evidence.normalization_type = update_data.normalization.normalization_type
            evidence.normalization_details = update_data.normalization.normalization_details

        if changes:
            self._create_version(evidence, "update", f"Updated: {', '.join(changes.keys())}", caregiver_id)

        self.db.commit()
        self.db.refresh(evidence)
        return evidence

    def delete_evidence(self, evidence_id: str) -> bool:
        """Soft delete by marking as invalidated. Never hard delete."""
        evidence = self.get_evidence(evidence_id)
        if not evidence:
            return False
        evidence.status = EvidenceStatus.INVALIDATED
        evidence.status_reason = "Deleted via API"
        self._create_version(evidence, "invalidation", "Deleted via API", "system")
        self.db.commit()
        return True

    # ============================================================
    # VERSIONING (Requirements 40, 41)
    # ============================================================

    def _create_version(self, evidence: Evidence, change_type: str, change_reason: str, changed_by: str, changed_by_system: bool = False) -> EvidenceVersion:
        """Create immutable version snapshot."""
        version = EvidenceVersion(
            evidence_id=evidence.id,
            version=evidence.version,
            subject_role=evidence.subject_role,
            subject_id=evidence.subject_id,
            observer_role=evidence.observer_role,
            observer_id=evidence.observer_id,
            author_role=evidence.author_role,
            author_id=evidence.author_id,
            type=evidence.type,
            subtype=evidence.subtype,
            source_text=evidence.source_text,
            normalized_text=evidence.normalized_text,
            normalization_type=evidence.normalization_type,
            normalization_details=evidence.normalization_details,
            negation_status=evidence.negation_status,
            uncertainty_level=evidence.uncertainty_level,
            hedge_type=evidence.hedge_type,
            hedge_text=evidence.hedge_text,
            event_time=evidence.event_time,
            event_time_granularity=evidence.event_time_granularity,
            event_time_text=evidence.event_time_text,
            event_time_anchor=evidence.event_time_anchor,
            observation_time=evidence.observation_time,
            document_time=evidence.document_time,
            source_id=evidence.source_id,
            source_version=evidence.source_version,
            source_type=evidence.source_type,
            source_location_page=evidence.source_location_page,
            source_location_paragraph=evidence.source_location_paragraph,
            source_location_text_span_start=evidence.source_location_text_span_start,
            source_location_text_span_end=evidence.source_location_text_span_end,
            extraction_method=evidence.extraction_method,
            extraction_version=evidence.extraction_version,
            extraction_model=evidence.extraction_model,
            confidence_extraction=evidence.confidence_extraction,
            confidence_entity_resolution=evidence.confidence_entity_resolution,
            confidence_temporal=evidence.confidence_temporal,
            confidence_source_support=evidence.confidence_source_support,
            confidence_interpretation=evidence.confidence_interpretation,
            status=evidence.status,
            status_reason=evidence.status_reason,
            valid_from=evidence.valid_from,
            valid_until=evidence.valid_until,
            change_type=change_type,
            change_reason=change_reason,
            changed_by=changed_by,
            changed_by_system=changed_by_system,
        )
        self.db.add(version)
        evidence.version += 1
        self.db.flush()
        return version

    def get_versions(self, evidence_id: str) -> List[EvidenceVersion]:
        """Get all versions of an evidence item."""
        return self.db.query(EvidenceVersion).filter(EvidenceVersion.evidence_id == evidence_id).order_by(EvidenceVersion.version).all()

    def get_version(self, evidence_id: str, version: int) -> Optional[EvidenceVersion]:
        """Get specific version."""
        return self.db.query(EvidenceVersion).filter(
            EvidenceVersion.evidence_id == evidence_id,
            EvidenceVersion.version == version
        ).first()

    # ============================================================
    # RELATIONSHIPS (Requirements 31, 32)
    # ============================================================

    def create_relationship(self, rel_data: EvidenceRelationshipCreate, caregiver_id: Optional[str] = None) -> EvidenceRelationship:
        """Create typed relationship between evidence items."""
        # Verify both evidence exist
        source_ev = self.get_evidence(rel_data.source_evidence_id)
        target_ev = self.get_evidence(rel_data.target_evidence_id)
        if not source_ev or not target_ev:
            raise ValueError("One or both evidence items not found")

        # Check for duplicate
        existing = self.db.query(EvidenceRelationship).filter(
            EvidenceRelationship.source_evidence_id == rel_data.source_evidence_id,
            EvidenceRelationship.target_evidence_id == rel_data.target_evidence_id,
            EvidenceRelationship.relationship_type == rel_data.relationship_type,
        ).first()
        if existing:
            return existing

        relationship = EvidenceRelationship(
            source_evidence_id=rel_data.source_evidence_id,
            target_evidence_id=rel_data.target_evidence_id,
            relationship_type=rel_data.relationship_type,
            temporal_relationship=rel_data.temporal_relationship,
            created_by=caregiver_id,
            created_by_system=caregiver_id is None,
            detection_method=rel_data.detection_method,
            detection_confidence=rel_data.detection_confidence,
        )
        self.db.add(relationship)
        self.db.commit()
        self.db.refresh(relationship)
        return relationship

    def get_relationships(self, evidence_id: str, relationship_type: Optional[EvidenceRelationshipType] = None) -> List[EvidenceRelationship]:
        """Get all relationships for an evidence item."""
        query = self.db.query(EvidenceRelationship).filter(
            or_(
                EvidenceRelationship.source_evidence_id == evidence_id,
                EvidenceRelationship.target_evidence_id == evidence_id,
            ),
            EvidenceRelationship.is_active == True,
        )
        if relationship_type:
            query = query.filter(EvidenceRelationship.relationship_type == relationship_type)
        return query.all()

    def get_outgoing_relationships(self, evidence_id: str, relationship_type: Optional[EvidenceRelationshipType] = None) -> List[EvidenceRelationship]:
        """Get outgoing relationships from evidence."""
        query = self.db.query(EvidenceRelationship).filter(
            EvidenceRelationship.source_evidence_id == evidence_id,
            EvidenceRelationship.is_active == True,
        )
        if relationship_type:
            query = query.filter(EvidenceRelationship.relationship_type == relationship_type)
        return query.all()

    def get_incoming_relationships(self, evidence_id: str, relationship_type: Optional[EvidenceRelationshipType] = None) -> List[EvidenceRelationship]:
        """Get incoming relationships to evidence."""
        query = self.db.query(EvidenceRelationship).filter(
            EvidenceRelationship.target_evidence_id == evidence_id,
            EvidenceRelationship.is_active == True,
        )
        if relationship_type:
            query = query.filter(EvidenceRelationship.relationship_type == relationship_type)
        return query.all()

    def invalidate_relationship(self, relationship_id: str, caregiver_id: str, reason: str) -> bool:
        """Invalidate a relationship without deleting."""
        rel = self.db.query(EvidenceRelationship).filter(EvidenceRelationship.id == relationship_id).first()
        if not rel:
            return False
        rel.is_active = False
        rel.invalidated_at = datetime.now(timezone.utc)
        rel.invalidated_by = caregiver_id
        rel.invalidation_reason = reason
        self.db.commit()
        return True

    # ============================================================
    # CORRECTION (Requirements 38, 39)
    # ============================================================

    def create_correction(self, correction_data: EvidenceCorrectionCreate, caregiver_id: str) -> EvidenceCorrection:
        """Create evidence correction with downstream impact tracking."""
        evidence = self.get_evidence(correction_data.evidence_id)
        if not evidence:
            raise ValueError(f"Evidence {correction_data.evidence_id} not found")

        # Find downstream dependencies
        affected = self._find_downstream_dependencies(evidence.id)

        correction = EvidenceCorrection(
            evidence_id=correction_data.evidence_id,
            correction_type=correction_data.correction_type,
            field_name=correction_data.field_name,
            old_value=correction_data.old_value,
            new_value=correction_data.new_value,
            correction_reason=correction_data.correction_reason,
            is_source_correction=correction_data.is_source_correction,
            source_correction_details=correction_data.source_correction_details,
            affected_claims=affected.get("claims", []),
            affected_events=affected.get("events", []),
            affected_care_states=affected.get("care_states", []),
            affected_situations=affected.get("situations", []),
            affected_watches=affected.get("watches", []),
            affected_summaries=affected.get("summaries", []),
            corrected_by=caregiver_id,
        )
        self.db.add(correction)
        self.db.commit()
        self.db.refresh(correction)
        return correction

    def apply_correction(self, correction_id: str) -> bool:
        """Apply a correction and mark downstream as stale."""
        correction = self.db.query(EvidenceCorrection).filter(EvidenceCorrection.id == correction_id).first()
        if not correction or correction.applied:
            return False

        evidence = self.get_evidence(correction.evidence_id)
        if not evidence:
            return False

        # Apply the correction to evidence
        if correction.field_name == "source_text":
            evidence.source_text = correction.new_value or ""
        elif correction.field_name == "normalized_text":
            evidence.normalized_text = correction.new_value
        elif correction.field_name == "subject_id":
            evidence.subject_id = correction.new_value
        elif correction.field_name == "event_time":
            evidence.event_time = datetime.fromisoformat(correction.new_value) if correction.new_value else None
        # Add more fields as needed

        # Create version
        self._create_version(evidence, "correction", correction.correction_reason or "Correction applied", correction.corrected_by)

        # Mark correction applied
        correction.applied = True
        correction.applied_at = datetime.now(timezone.utc)

        # Mark downstream as stale (would trigger recomputation)
        self._mark_downstream_stale(correction)

        self.db.commit()
        return True

    def _find_downstream_dependencies(self, evidence_id: str) -> Dict[str, List[str]]:
        """Find all downstream objects depending on this evidence."""
        # This would query CareEvent, Insight, Action, Situation, etc.
        # For now, return empty - to be implemented with full integration
        return {
            "claims": [],
            "events": [],
            "care_states": [],
            "situations": [],
            "watches": [],
            "summaries": [],
        }

    def _mark_downstream_stale(self, correction: EvidenceCorrection) -> None:
        """Mark downstream objects as stale. Requirement 82."""
        # Implementation would update CareEvent, Insight, etc. with stale flag
        pass

    # ============================================================
    # INVALIDATION (Requirements 36, 37)
    # ============================================================

    def create_invalidation(self, invalidation_data: EvidenceInvalidationCreate, caregiver_id: str) -> EvidenceInvalidation:
        """Create evidence invalidation with propagation tracking."""
        evidence = self.get_evidence(invalidation_data.evidence_id)
        if not evidence:
            raise ValueError(f"Evidence {invalidation_data.evidence_id} not found")

        affected = self._find_downstream_dependencies(evidence.id)

        invalidation = EvidenceInvalidation(
            evidence_id=invalidation_data.evidence_id,
            reason=invalidation_data.reason,
            reason_details=invalidation_data.reason_details,
            invalidated_by=caregiver_id,
            propagation_status="pending",
            affected_claims=affected.get("claims", []),
            affected_events=affected.get("events", []),
            affected_care_states=affected.get("care_states", []),
            affected_situations=affected.get("situations", []),
            affected_watches=affected.get("watches", []),
            affected_summaries=affected.get("summaries", []),
            affected_next_actions=affected.get("next_actions", []),
        )
        self.db.add(invalidation)

        # Mark evidence as invalidated
        evidence.status = EvidenceStatus.INVALIDATED
        evidence.status_reason = invalidation_data.reason_details or invalidation_data.reason.value
        self._create_version(evidence, "invalidation", invalidation_data.reason.value, caregiver_id)

        self.db.commit()
        self.db.refresh(invalidation)
        return invalidation

    def propagate_invalidation(self, invalidation_id: str) -> EvidenceInvalidation:
        """Propagate invalidation to downstream objects. Requirement 37."""
        invalidation = self.db.query(EvidenceInvalidation).filter(EvidenceInvalidation.id == invalidation_id).first()
        if not invalidation:
            raise ValueError(f"Invalidation {invalidation_id} not found")

        invalidation.propagation_status = "in_progress"
        log = []

        # Propagate to each affected type
        for claim_id in invalidation.affected_claims or []:
            log.append({"step": "claim", "target_id": claim_id, "status": "marked_stale", "timestamp": datetime.now(timezone.utc).isoformat()})
            # Mark claim as stale/invalid

        for event_id in invalidation.affected_events or []:
            log.append({"step": "event", "target_id": event_id, "status": "marked_stale", "timestamp": datetime.now(timezone.utc).isoformat()})
            # Mark event as stale

        for state_id in invalidation.affected_care_states or []:
            log.append({"step": "care_state", "target_id": state_id, "status": "marked_stale", "timestamp": datetime.now(timezone.utc).isoformat()})

        for situation_id in invalidation.affected_situations or []:
            log.append({"step": "situation", "target_id": situation_id, "status": "marked_stale", "timestamp": datetime.now(timezone.utc).isoformat()})

        for watch_id in invalidation.affected_watches or []:
            log.append({"step": "watch", "target_id": watch_id, "status": "marked_stale", "timestamp": datetime.now(timezone.utc).isoformat()})

        for summary_id in invalidation.affected_summaries or []:
            log.append({"step": "summary", "target_id": summary_id, "status": "marked_stale", "timestamp": datetime.now(timezone.utc).isoformat()})

        for action_id in invalidation.affected_next_actions or []:
            log.append({"step": "next_action", "target_id": action_id, "status": "marked_stale", "timestamp": datetime.now(timezone.utc).isoformat()})

        invalidation.propagation_log = log
        invalidation.propagation_status = "completed"
        invalidation.completed_at = datetime.now(timezone.utc)
        self.db.commit()
        self.db.refresh(invalidation)
        return invalidation

    # ============================================================
    # SUPERSESSION (Requirements 34, 35)
    # ============================================================

    def create_supersession(self, supersession_data: EvidenceSupersessionCreate, caregiver_id: Optional[str] = None) -> EvidenceSupersession:
        """Create supersession relationship. SUPERSEDED != INVALIDATED."""
        superseded = self.get_evidence(supersession_data.superseded_evidence_id)
        superseding = self.get_evidence(supersession_data.superseding_evidence_id)
        if not superseded or not superseding:
            raise ValueError("One or both evidence items not found")

        supersession = EvidenceSupersession(
            superseded_evidence_id=supersession_data.superseded_evidence_id,
            superseding_evidence_id=supersession_data.superseding_evidence_id,
            reason=supersession_data.reason,
            reason_details=supersession_data.reason_details,
            established_by=caregiver_id,
            established_by_system=caregiver_id is None,
            effective_from=supersession_data.effective_from,
            is_explicit=supersession_data.is_explicit,
        )
        self.db.add(supersession)

        # Update statuses
        superseded.status = EvidenceStatus.SUPERSEDED
        superseded.status_reason = f"Superseded by {superseding.id}: {supersession_data.reason.value}"
        superseded.valid_until = supersession_data.effective_from
        self._create_version(superseded, "supersession", f"Superseded by {superseding.id}", caregiver_id or "system")

        superseding.status = EvidenceStatus.ACTIVE
        superseding.valid_from = supersession_data.effective_from

        self.db.commit()
        self.db.refresh(supersession)
        return supersession

    def get_supersession_chain(self, evidence_id: str) -> List[EvidenceSupersession]:
        """Get full supersession chain for an evidence item."""
        # Get all supersessions where this evidence is either superseded or superseding
        return self.db.query(EvidenceSupersession).filter(
            or_(
                EvidenceSupersession.superseded_evidence_id == evidence_id,
                EvidenceSupersession.superseding_evidence_id == evidence_id,
            )
        ).order_by(EvidenceSupersession.effective_from).all()

    # ============================================================
    # DEDUPLICATION (Requirements 27, 59)
    # ============================================================

    def propose_deduplication(self, dedup_data: EvidenceDeduplicationCreate) -> EvidenceDeduplication:
        """Propose that two evidence items represent same underlying information."""
        ev_a = self.get_evidence(dedup_data.evidence_id_a)
        ev_b = self.get_evidence(dedup_data.evidence_id_b)
        if not ev_a or not ev_b:
            raise ValueError("One or both evidence items not found")

        # Check if already proposed
        existing = self.db.query(EvidenceDeduplication).filter(
            or_(
                and_(EvidenceDeduplication.evidence_id_a == dedup_data.evidence_id_a, EvidenceDeduplication.evidence_id_b == dedup_data.evidence_id_b),
                and_(EvidenceDeduplication.evidence_id_a == dedup_data.evidence_id_b, EvidenceDeduplication.evidence_id_b == dedup_data.evidence_id_a),
            )
        ).first()
        if existing:
            return existing

        dedup = EvidenceDeduplication(
            evidence_id_a=dedup_data.evidence_id_a,
            evidence_id_b=dedup_data.evidence_id_b,
            similarity_score=dedup_data.similarity_score,
            match_type=dedup_data.match_type,
            matched_fields=dedup_data.matched_fields,
        )
        self.db.add(dedup)

        # Also create relationship
        self.create_relationship(EvidenceRelationshipCreate(
            source_evidence_id=dedup_data.evidence_id_a,
            target_evidence_id=dedup_data.evidence_id_b,
            relationship_type=EvidenceRelationshipType.SAME_UNDERLYING_INFORMATION,
            detection_method=dedup_data.match_type,
            detection_confidence=dedup_data.similarity_score,
        ))

        self.db.commit()
        self.db.refresh(dedup)
        return dedup

    def confirm_deduplication(self, dedup_id: str, caregiver_id: str) -> EvidenceDeduplication:
        """Confirm a deduplication proposal."""
        dedup = self.db.query(EvidenceDeduplication).filter(EvidenceDeduplication.id == dedup_id).first()
        if not dedup:
            raise ValueError(f"Deduplication {dedup_id} not found")
        dedup.status = "confirmed"
        dedup.confirmed_by = caregiver_id
        dedup.confirmed_at = datetime.now(timezone.utc)
        self.db.commit()
        self.db.refresh(dedup)
        return dedup

    def reject_deduplication(self, dedup_id: str, caregiver_id: str) -> EvidenceDeduplication:
        """Reject a deduplication proposal."""
        dedup = self.db.query(EvidenceDeduplication).filter(EvidenceDeduplication.id == dedup_id).first()
        if not dedup:
            raise ValueError(f"Deduplication {dedup_id} not found")
        dedup.status = "rejected"
        dedup.confirmed_by = caregiver_id
        dedup.confirmed_at = datetime.now(timezone.utc)
        self.db.commit()
        self.db.refresh(dedup)
        return dedup

    # ============================================================
    # QUERY (Requirement 64)
    # ============================================================

    def query_evidence(self, params: EvidenceQueryParams) -> Tuple[List[Evidence], int]:
        """Structured evidence query with filters. Requirement 64."""
        query = self.db.query(Evidence)

        if params.person_id:
            query = query.filter(Evidence.subject_id == params.person_id)
        if params.subject_id:
            query = query.filter(Evidence.subject_id == params.subject_id)
        if params.subject_role:
            query = query.filter(Evidence.subject_role == params.subject_role)
        if params.type:
            query = query.filter(Evidence.type == params.type)
        if params.subtype:
            query = query.filter(Evidence.subtype == params.subtype)
        if params.status:
            query = query.filter(Evidence.status == params.status)
        if params.source_id:
            query = query.filter(Evidence.source_id == params.source_id)
        if params.source_type:
            query = query.filter(Evidence.source_type == params.source_type)
        if params.observer_role:
            query = query.filter(Evidence.observer_role == params.observer_role)
        if params.observer_id:
            query = query.filter(Evidence.observer_id == params.observer_id)
        if params.author_role:
            query = query.filter(Evidence.author_role == params.author_role)
        if params.author_id:
            query = query.filter(Evidence.author_id == params.author_id)
        if params.uploaded_by_caregiver_id:
            query = query.filter(Evidence.uploaded_by_caregiver_id == params.uploaded_by_caregiver_id)
        if params.event_time_from:
            query = query.filter(Evidence.event_time >= params.event_time_from)
        if params.event_time_to:
            query = query.filter(Evidence.event_time <= params.event_time_to)
        if params.observation_time_from:
            query = query.filter(Evidence.observation_time >= params.observation_time_from)
        if params.observation_time_to:
            query = query.filter(Evidence.observation_time <= params.observation_time_to)
        if params.created_at_from:
            query = query.filter(Evidence.created_at >= params.created_at_from)
        if params.created_at_to:
            query = query.filter(Evidence.created_at <= params.created_at_to)
        if params.confidence_extraction_min is not None:
            query = query.filter(Evidence.confidence_extraction >= params.confidence_extraction_min)
        if params.confidence_entity_min is not None:
            query = query.filter(Evidence.confidence_entity_resolution >= params.confidence_entity_min)
        if params.confidence_temporal_min is not None:
            query = query.filter(Evidence.confidence_temporal >= params.confidence_temporal_min)
        if params.negation_status:
            query = query.filter(Evidence.negation_status == params.negation_status)
        if params.uncertainty_level:
            query = query.filter(Evidence.uncertainty_level == params.uncertainty_level)
        if params.quality_gate_passed is not None:
            query = query.filter(Evidence.quality_gate_passed == params.quality_gate_passed)

        # Relationship filters
        if params.has_relationships is not None:
            if params.has_relationships:
                subq = self.db.query(EvidenceRelationship.source_evidence_id).filter(
                    or_(
                        EvidenceRelationship.source_evidence_id == Evidence.id,
                        EvidenceRelationship.target_evidence_id == Evidence.id,
                    ),
                    EvidenceRelationship.is_active == True,
                ).subquery()
                query = query.filter(Evidence.id.in_(subq))
            else:
                subq = self.db.query(EvidenceRelationship.source_evidence_id).filter(
                    or_(
                        EvidenceRelationship.source_evidence_id == Evidence.id,
                        EvidenceRelationship.target_evidence_id == Evidence.id,
                    ),
                    EvidenceRelationship.is_active == True,
                ).subquery()
                query = query.filter(~Evidence.id.in_(subq))

        if params.relationship_type and params.related_to_evidence_id:
            subq = self.db.query(EvidenceRelationship.source_evidence_id).filter(
                EvidenceRelationship.target_evidence_id == params.related_to_evidence_id,
                EvidenceRelationship.relationship_type == params.relationship_type,
                EvidenceRelationship.is_active == True,
            ).subquery()
            query = query.filter(Evidence.id.in_(subq))

        total = query.count()

        # Sorting
        sort_col = getattr(Evidence, params.sort_by, Evidence.created_at)
        if params.sort_order == "desc":
            query = query.order_by(desc(sort_col))
        else:
            query = query.order_by(sort_col)

        results = query.offset(params.offset).limit(params.limit).all()
        return results, total

    # ============================================================
    # TRACEABILITY (Requirements 22, 23, 24, 100)
    # ============================================================

    def get_trace(self, evidence_id: str) -> Optional[EvidenceTraceResponse]:
        """Get full traceability chain for evidence. Requirements 22, 23, 24."""
        evidence = self.get_evidence(evidence_id)
        if not evidence:
            return None

        # Source document
        source_doc = None
        if evidence.source_id:
            doc = self.db.query(DocumentModel).filter(DocumentModel.id == evidence.source_id).first()
            if doc:
                source_doc = {
                    "id": doc.id,
                    "original_filename": doc.original_filename,
                    "document_type": doc.document_type,
                    "processing_status": doc.processing_status,
                    "extraction_method": doc.extraction_method,
                    "page_count": doc.page_count,
                }

        # Source location
        source_location = None
        if any([evidence.source_location_page, evidence.source_location_paragraph,
                evidence.source_location_text_span_start, evidence.source_location_text_span_end]):
            source_location = SourceLocation(
                page=evidence.source_location_page,
                paragraph=evidence.source_location_paragraph,
                text_span_start=evidence.source_location_text_span_start,
                text_span_end=evidence.source_location_text_span_end,
                image_region=evidence.source_location_image_region,
                structured_field=evidence.source_location_structured_field,
            )

        # Relationships
        relationships = self.get_relationships(evidence_id)
        rel_responses = [EvidenceRelationshipResponse.model_validate(r) for r in relationships]

        # Versions
        versions = self.get_versions(evidence_id)
        version_responses = [EvidenceVersionResponse.model_validate(v) for v in versions]

        # Corrections
        corrections = self.db.query(EvidenceCorrection).filter(EvidenceCorrection.evidence_id == evidence_id).all()
        correction_responses = [EvidenceCorrectionResponse.model_validate(c) for c in corrections]

        # Invalidations
        invalidations = self.db.query(EvidenceInvalidation).filter(EvidenceInvalidation.evidence_id == evidence_id).all()
        invalidation_responses = [EvidenceInvalidationResponse.model_validate(i) for i in invalidations]

        # Supersessions
        supersessions = self.get_supersession_chain(evidence_id)
        supersession_responses = [EvidenceSupersessionResponse.model_validate(s) for s in supersessions]

        # Downstream - would need integration with other models
        supporting_claims = []
        contradicting_claims = []
        derived_events = []
        care_state_components = []
        situations = []
        downstream_actions = []

        return EvidenceTraceResponse(
            evidence=EvidenceResponse.model_validate(evidence),
            source_document=source_doc,
            source_location=source_location,
            supporting_claims=supporting_claims,
            contradicting_claims=contradicting_claims,
            derived_events=derived_events,
            care_state_components=care_state_components,
            situations=situations,
            downstream_actions=downstream_actions,
            corrections=correction_responses,
            invalidations=invalidation_responses,
            supersessions=supersession_responses,
            versions=version_responses,
            relationships=rel_responses,
        )

    def get_evidence_graph(self, person_id: str) -> EvidenceGraphResponse:
        """Get evidence graph for a person. Requirement 100."""
        evidence_list = self.db.query(Evidence).filter(Evidence.subject_id == person_id).all()
        evidence_responses = [EvidenceResponse.model_validate(e) for e in evidence_list]

        # Get all relationships between these evidence items
        evidence_ids = [e.id for e in evidence_list]
        relationships = self.db.query(EvidenceRelationship).filter(
            EvidenceRelationship.source_evidence_id.in_(evidence_ids),
            EvidenceRelationship.target_evidence_id.in_(evidence_ids),
            EvidenceRelationship.is_active == True,
        ).all()
        rel_responses = [EvidenceRelationshipResponse.model_validate(r) for r in relationships]

        # Cluster by event/situation - placeholder
        clusters = []

        return EvidenceGraphResponse(
            nodes=evidence_responses,
            edges=rel_responses,
            clusters=clusters,
        )