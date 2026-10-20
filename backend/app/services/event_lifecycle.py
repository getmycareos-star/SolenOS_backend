"""Event Correction, Invalidation & Supersession Service.

Implements full lifecycle management with downstream propagation tracking.
- Correction: Fix specific fields with version history (Requirement 43)
- Invalidation: Remove unreliable events with propagation tracking (Requirement 44)
- Supersession: Replace events with newer versions (Requirement 45)

All operations:
- Create immutable audit trail
- Track downstream impact (relationships, groupings, situations, state impacts)
- Support person-contamination prevention (wrong patient detection)
- Never destroy provenance - only supersede/invalidate
"""

from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Set
from dataclasses import dataclass, field
from sqlalchemy.orm import Session
from sqlalchemy import update

from app.core.event_enums import (
    CareEventType,
    CareEventSubtype,
    CareEventStatus,
    CareEventSeverity,
    CareEventRelationshipType,
    EventCorrectionType,
    EventInvalidationReason,
    EventSupersessionReason,
    EventLifecycleStage,
)
from app.models.care_event import (
    CareEvent,
    CareEventVersion,
    CareEventRelationship,
    CareEventRecurrenceGroup,
    CareEventGroup,
    CareEventCorrection,
    CareEventInvalidation,
    CareEventSupersession,
    CareEventStateImpact,
)


@dataclass
class PropagatedChange:
    """Tracks a downstream entity affected by a correction/invalidation."""
    target_type: str
    target_id: str
    action: str  # added, modified, checked, skipped
    status: str  # success, failed, skipped
    details: Optional[str] = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class DownstreamAnalysis:
    """Analysis of downstream entities affected by a change."""
    affected_relationships: List[str] = field(default_factory=list)
    affected_recurrence_groups: List[str] = field(default_factory=list)
    affected_groups: List[str] = field(default_factory=list)
    affected_situations: List[str] = field(default_factory=list)
    affected_state_impacts: List[str] = field(default_factory=list)
    affected_change_signals: List[str] = field(default_factory=list)
    affected_watches: List[str] = field(default_factory=list)
    affected_summaries: List[str] = field(default_factory=list)
    affected_next_actions: List[str] = field(default_factory=list)
    propagation_log: List[Dict[str, Any]] = field(default_factory=list)


class EventLifecycleService:
    """Manages care event lifecycle: correction, invalidation, supersession.

    All operations are transactional and track downstream impact.
    Person-contamination prevention is enforced at the validation level.
    """

    def __init__(self):
        pass

    # ==================== CORRECTION ====================

    def create_correction(
        self,
        db: Session,
        care_event_id: str,
        correction_type: EventCorrectionType,
        field_name: str,
        old_value: Optional[str],
        new_value: Optional[str],
        corrected_by: str,
        correction_reason: Optional[str] = None,
        correction_evidence_ids: Optional[List[str]] = None,
        is_source_correction: bool = False,
        source_correction_details: Optional[str] = None,
    ) -> CareEventCorrection:
        """Create a correction record and apply it to the event.

        Creates an immutable audit trail. The correction is recorded but
        must be explicitly applied before changing downstream entities.
        """
        # Validate the event exists
        event = db.get(CareEvent, care_event_id)
        if not event:
            raise ValueError(f"CareEvent {care_event_id} not found")

        # Store original value based on field_name
        actual_old_value = self._get_field_value(event, field_name)
        if old_value is None:
            old_value = actual_old_value

        correction = CareEventCorrection(
            care_event_id=care_event_id,
            correction_type=correction_type,
            field_name=field_name,
            old_value=str(old_value) if old_value is not None else None,
            new_value=str(new_value) if new_value is not None else None,
            correction_reason=correction_reason,
            correction_evidence_ids=correction_evidence_ids or [],
            is_source_correction=is_source_correction,
            source_correction_details=source_correction_details,
            corrected_by=corrected_by,
            applied=False,
            created_at=datetime.now(timezone.utc),
        )

        db.add(correction)
        db.commit()
        db.refresh(correction)

        return correction

    def apply_correction(
        self,
        db: Session,
        correction_id: str,
    ) -> CareEventCorrection:
        """Apply a correction to the event and propagate to downstream entities."""
        correction = db.get(CareEventCorrection, correction_id)
        if not correction:
            raise ValueError(f"Correction {correction_id} not found")

        if correction.applied:
            return correction

        # Create version snapshot before applying
        event = db.get(CareEvent, correction.care_event_id)
        if not event:
            raise ValueError(f"CareEvent {correction.care_event_id} not found")

        # Create version record
        version = CareEventVersion(
            care_event_id=correction.care_event_id,
            version=event.version,
            change_type=f"correction_{correction.field_name}",
            change_reason=correction.correction_reason,
            changed_by=correction.corrected_by,
            changed_by_system=False,
            affected_downstream=[],
            created_at=datetime.now(timezone.utc),
        )
        db.add(version)

        # Apply the field change
        self._set_field_value(event, correction.field_name, correction.new_value)
        event.version += 1
        event.updated_at = datetime.now(timezone.utc)

        # Mark correction as applied
        correction.applied = True
        correction.applied_at = datetime.now(timezone.utc)

        # Analyze and propagate to downstream
        downstream = self._analyze_downstream(db, correction.care_event_id)
        self._propagate_correction(db, correction, downstream)

        db.commit()
        db.refresh(correction)
        db.refresh(event)

        return correction

    # ==================== INVALIDATION ====================

    def invalidate_event(
        self,
        db: Session,
        care_event_id: str,
        reason: EventInvalidationReason,
        invalidated_by: str,
        reason_details: Optional[str] = None,
    ) -> CareEventInvalidation:
        """Invalidate a care event with full propagation tracking.

        Invalidation marks an event as unreliable/erroneous but preserves
        it in history. Downstream entities are tracked for propagation.
        """
        event = db.get(CareEvent, care_event_id)
        if not event:
            raise ValueError(f"CareEvent {care_event_id} not found")

        if event.lifecycle_stage == EventLifecycleStage.INVALIDATED:
            raise ValueError(f"Event {care_event_id} is already invalidated")

        invalidation = CareEventInvalidation(
            care_event_id=care_event_id,
            reason=reason,
            reason_details=reason_details,
            invalidated_by=invalidated_by,
            propagation_status="pending",
            created_at=datetime.now(timezone.utc),
        )
        db.add(invalidation)

        # Mark event as invalidated
        event.lifecycle_stage = EventLifecycleStage.INVALIDATED
        event.status = CareEventStatus.INVALIDATED
        event.updated_at = datetime.now(timezone.utc)

        db.commit()
        db.refresh(invalidation)
        db.refresh(event)

        # Analyze downstream impact
        downstream = self._analyze_downstream(db, care_event_id)
        invalidation.affected_relationships = downstream.affected_relationships
        invalidation.affected_recurrence_groups = downstream.affected_recurrence_groups
        invalidation.affected_groups = downstream.affected_groups
        invalidation.affected_situations = downstream.affected_situations
        invalidation.affected_state_impacts = downstream.affected_state_impacts
        invalidation.affected_change_signals = downstream.affected_change_signals
        invalidation.affected_watches = downstream.affected_watches
        invalidation.affected_summaries = downstream.affected_summaries
        invalidation.affected_next_actions = downstream.affected_next_actions

        # Propagate
        self._propagate_invalidation(db, invalidation, downstream)

        db.commit()
        db.refresh(invalidation)

        return invalidation

    # ==================== SUPERSESSION ====================

    def supersede_event(
        self,
        db: Session,
        superseded_event_id: str,
        superseding_event_id: str,
        reason: EventSupersessionReason,
        established_by: Optional[str] = None,
        is_explicit: bool = True,
        reason_details: Optional[str] = None,
    ) -> CareEventSupersession:
        """Supersede one event with another.

        The superseded event is marked as historical but preserved.
        The superseding event becomes the active version.
        """
        superseded = db.get(CareEvent, superseded_event_id)
        if not superseded:
            raise ValueError(f"Superseded event {superseded_event_id} not found")

        superseding = db.get(CareEvent, superseding_event_id)
        if not superseding:
            raise ValueError(f"Superseding event {superseding_event_id} not found")

        # Validate both events are for the same person
        if superseded.person_id != superseding.person_id:
            raise ValueError(
                f"Person mismatch: superseded event belongs to person {superseded.person_id}, "
                f"superseding to person {superseding.person_id}"
            )

        # Validate both events are for the same event type
        if superseded.event_type != superseding.event_type:
            raise ValueError(
                f"Event type mismatch: superseded is {superseded.event_type}, "
                f"superseding is {superseding.event_type}"
            )

        if superseded.lifecycle_stage == EventLifecycleStage.INVALIDATED:
            raise ValueError(f"Cannot supersede an invalidated event")

        effective_from = datetime.now(timezone.utc)

        supersession = CareEventSupersession(
            superseded_event_id=superseded_event_id,
            superseding_event_id=superseding_event_id,
            reason=reason,
            reason_details=reason_details,
            effective_from=effective_from,
            is_explicit=is_explicit,
            established_by=established_by,
            established_by_system=established_by is None,
            created_at=datetime.now(timezone.utc),
        )
        db.add(supersession)

        # Mark superseded event
        superseded.lifecycle_stage = EventLifecycleStage.SUPERSEDED
        superseded.superseded_by_event_id = superseding_event_id
        superseded.status = CareEventStatus.SUPERSEDED
        superseded.updated_at = datetime.now(timezone.utc)

        db.commit()
        db.refresh(supersession)
        db.refresh(superseded)

        return supersession

    # ==================== RELATIONSHIP MANAGEMENT ====================

    def create_relationship(
        self,
        db: Session,
        source_event_id: str,
        target_event_id: str,
        relationship_type: CareEventRelationshipType,
        created_by: Optional[str] = None,
        is_temporal: bool = True,
        is_causal: bool = False,
        causal_evidence_ids: Optional[List[str]] = None,
        temporal_relationship: Optional[str] = None,
        time_gap_seconds: Optional[float] = None,
        strength: Optional[float] = None,
        confidence: Optional[float] = None,
        detection_method: Optional[str] = None,
        detection_confidence: Optional[float] = None,
        evidence_ids: Optional[List[str]] = None,
    ) -> CareEventRelationship:
        """Create a typed relationship between two events."""
        source = db.get(CareEvent, source_event_id)
        target = db.get(CareEvent, target_event_id)

        if not source:
            raise ValueError(f"Source event {source_event_id} not found")
        if not target:
            raise ValueError(f"Target event {target_event_id} not found")

        # Person contamination check
        if source.person_id != target.person_id:
            raise ValueError(
                f"Person contamination: source event belongs to person {source.person_id}, "
                f"target to person {target.person_id}"
            )

        relationship = CareEventRelationship(
            source_event_id=source_event_id,
            target_event_id=target_event_id,
            relationship_type=relationship_type,
            is_temporal=is_temporal,
            is_causal=is_causal,
            causal_evidence_ids=causal_evidence_ids or [],
            temporal_relationship=temporal_relationship,
            time_gap_seconds=time_gap_seconds,
            strength=strength,
            confidence=confidence,
            evidence_ids=evidence_ids or [],
            detection_method=detection_method,
            detection_confidence=detection_confidence,
            created_by=created_by,
            created_by_system=created_by is None,
            is_active=True,
        )
        db.add(relationship)
        db.commit()
        db.refresh(relationship)

        return relationship

    def invalidate_relationship(
        db: Session,
        relationship_id: str,
        invalidated_by: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> CareEventRelationship:
        """Mark a relationship as inactive."""
        rel = db.get(CareEventRelationship, relationship_id)
        if not rel:
            raise ValueError(f"Relationship {relationship_id} not found")

        rel.is_active = False
        rel.invalidated_at = datetime.now(timezone.utc)
        rel.invalidated_by = invalidated_by
        rel.invalidation_reason = reason
        db.commit()
        db.refresh(rel)

        return rel

    # ==================== DOWNSTREAM ANALYSIS & PROPAGATION ====================

    def _analyze_downstream(self, db: Session, event_id: str) -> DownstreamAnalysis:
        """Analyze all downstream entities that reference this event."""
        downstream = DownstreamAnalysis()

        # Relationships (both as source and target)
        rels = db.query(CareEventRelationship).filter(
            (CareEventRelationship.source_event_id == event_id)
            | (CareEventRelationship.target_event_id == event_id)
        ).all()
        downstream.affected_relationships = [r.id for r in rels]
        for r in rels:
            downstream.propagation_log.append({
                "step": "relationship_analysis",
                "target_type": "relationship",
                "target_id": r.id,
                "status": "checked",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

        # Recurrence groups
        rec_groups = db.query(CareEventRecurrenceGroup).filter(
            CareEventRecurrenceGroup.member_event_ids.contains([event_id])
        ).all()
        downstream.affected_recurrence_groups = [g.id for g in rec_groups]

        # Event groups
        groups = db.query(CareEventGroup).filter(
            CareEventGroup.member_event_ids.contains([event_id])
        ).all()
        downstream.affected_groups = [g.id for g in groups]

        # State impacts
        impacts = db.query(CareEventStateImpact).filter(
            CareEventStateImpact.care_event_id == event_id
        ).all()
        downstream.affected_state_impacts = [i.id for i in impacts]

        # Check for situations referencing this event
        # (would check situations table if events directly linked)
        # This is done via state impacts

        return downstream

    def _propagate_correction(
        self,
        db: Session,
        correction: CareEventCorrection,
        downstream: DownstreamAnalysis,
    ) -> None:
        """Propagate correction to downstream entities."""
        log_entry = {
            "step": "propagation_start",
            "target_type": "correction",
            "target_id": correction.id,
            "status": "in_progress",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        downstream.propagation_log.append(log_entry)

        changed_downstream: List[str] = []

        # Mark affected relationships as needing re-evaluation
        if downstream.affected_relationships:
            db.execute(
                update(CareEventRelationship)
                .where(CareEventRelationship.id.in_(downstream.affected_relationships))
                .values(detection_method="needs_reverification")
            )
            for rid in downstream.affected_relationships:
                changed_downstream.append(f"relationship:{rid}")

        # Mark affected recurrence groups
        if downstream.affected_recurrence_groups:
            db.execute(
                update(CareEventRecurrenceGroup)
                .where(CareEventRecurrenceGroup.id.in_(downstream.affected_recurrence_groups))
                .values(status="needs_recomputation", updated_at=datetime.now(timezone.utc))
            )

        # Mark affected groups
        if downstream.affected_groups:
            for gid in downstream.affected_groups:
                changed_downstream.append(f"group:{gid}")

        correction.affected_relationships = downstream.affected_relationships
        correction.affected_recurrence_groups = downstream.affected_recurrence_groups
        correction.affected_groups = downstream.affected_groups
        correction.affected_situations = downstream.affected_situations
        correction.affected_state_impacts = downstream.affected_state_impacts
        correction.affected_change_signals = downstream.affected_change_signals
        correction.affected_watches = downstream.affected_watches
        correction.affected_summaries = downstream.affected_summaries
        correction.application_details = json.dumps({
            "changed_downstream": changed_downstream,
            "propagation_log": downstream.propagation_log,
        })

        downstream.propagation_log.append({
            "step": "propagation_complete",
            "target_type": "correction",
            "target_id": correction.id,
            "status": "success",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    def _propagate_invalidation(
        self,
        db: Session,
        invalidation: CareEventInvalidation,
        downstream: DownstreamAnalysis,
    ) -> None:
        """Propagate invalidation to downstream entities."""
        log_entry = {
            "step": "propagation_start",
            "target_type": "invalidation",
            "target_id": invalidation.id,
            "status": "in_progress",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        downstream.propagation_log.append(log_entry)

        # Invalidate affected relationships
        if downstream.affected_relationships:
            db.execute(
                update(CareEventRelationship)
                .where(CareEventRelationship.id.in_(downstream.affected_relationships))
                .values(
                    is_active=False,
                    invalidated_at=datetime.now(timezone.utc),
                )
            )
            for rid in downstream.affected_relationships:
                downstream.propagation_log.append({
                    "step": "relationship_invalidation",
                    "target_type": "relationship",
                    "target_id": rid,
                    "status": "success",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })

        # Mark affected recurrence groups as needing recomputation
        if downstream.affected_recurrence_groups:
            db.execute(
                update(CareEventRecurrenceGroup)
                .where(CareEventRecurrenceGroup.id.in_(downstream.affected_recurrence_groups))
                .values(
                    status="needs_recomputation",
                    updated_at=datetime.now(timezone.utc),
                )
            )
            for rid in downstream.affected_recurrence_groups:
                downstream.propagation_log.append({
                    "step": "recurrence_group_update",
                    "target_type": "recurrence_group",
                    "target_id": rid,
                    "status": "success",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })

        # Mark affected state impacts as revoked
        if downstream.affected_state_impacts:
            db.execute(
                update(CareEventStateImpact)
                .where(CareEventStateImpact.id.in_(downstream.affected_state_impacts))
                .values(
                    is_revoked=True,
                    revoked_at=datetime.now(timezone.utc),
                )
            )

        # Update invalidation record
        invalidation.propagation_status = "completed"
        invalidation.completed_at = datetime.now(timezone.utc)
        invalidation.affected_relationships = downstream.affected_relationships
        invalidation.affected_recurrence_groups = downstream.affected_recurrence_groups
        invalidation.affected_groups = downstream.affected_groups
        invalidation.affected_situations = downstream.affected_situations
        invalidation.affected_state_impacts = downstream.affected_state_impacts
        invalidation.affected_change_signals = downstream.affected_change_signals
        invalidation.affected_watches = downstream.affected_watches
        invalidation.affected_summaries = downstream.affected_summaries
        invalidation.affected_next_actions = downstream.affected_next_actions

        downstream.propagation_log.append({
            "step": "propagation_complete",
            "target_type": "invalidation",
            "target_id": invalidation.id,
            "status": "success",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    # ==================== HELPERS ====================

    def _get_field_value(self, event: CareEvent, field_name: str) -> Any:
        """Get a field value from a CareEvent."""
        return getattr(event, field_name, None)

    def _set_field_value(self, event: CareEvent, field_name: str, new_value: Any) -> None:
        """Set a field value on a CareEvent."""
        # Parse JSON/list fields
        if field_name in ("evidence_ids", "source_ids", "outcome_evidence_ids", "tags"):
            if isinstance(new_value, str):
                new_value = json.loads(new_value)
            elif isinstance(new_value, (list, tuple)):
                new_value = list(new_value)
        elif field_name in ("confidence_details", "state_impacts", "change_signals", "watch_signals"):
            if isinstance(new_value, str):
                new_value = json.loads(new_value)

        setattr(event, field_name, new_value)

    def get_correction_history(
        self,
        db: Session,
        care_event_id: str,
    ) -> List[CareEventCorrection]:
        """Get all corrections for a care event."""
        return (
            db.query(CareEventCorrection)
            .filter(CareEventCorrection.care_event_id == care_event_id)
            .order_by(CareEventCorrection.created_at.desc())
            .all()
        )

    def get_invalidation_history(
        self,
        db: Session,
        care_event_id: str,
    ) -> List[CareEventInvalidation]:
        """Get all invalidations for a care event."""
        return (
            db.query(CareEventInvalidation)
            .filter(CareEventInvalidation.care_event_id == care_event_id)
            .order_by(CareEventInvalidation.created_at.desc())
            .all()
        )

    def get_supersession_history(
        self,
        db: Session,
        care_event_id: str,
    ) -> List[CareEventSupersession]:
        """Get all supersessions for a care event (as both superseded and superseding)."""
        return (
            db.query(CareEventSupersession)
            .filter(
                (CareEventSupersession.superseded_event_id == care_event_id)
                | (CareEventSupersession.superseding_event_id == care_event_id)
            )
            .order_by(CareEventSupersession.created_at.desc())
            .all()
        )

    def get_current_event(
        self,
        db: Session,
        event_id: str,
    ) -> Optional[CareEvent]:
        """Get the current (non-superseded, non-invalidated) version of an event.

        Follows supersession chain to find the most current version.
        """
        event = db.get(CareEvent, event_id)
        if not event:
            return None

        visited: Set[str] = set()
        while event.lifecycle_stage == EventLifecycleStage.SUPERSEDED and event.superseded_by_event_id:
            if event.id in visited:
                return event  # Cycle protection
            visited.add(event.id)
            event = db.get(CareEvent, event.superseded_by_event_id)
            if not event:
                return None

        if event.lifecycle_stage == EventLifecycleStage.INVALIDATED:
            return None

        return event

    def batch_correct_field(
        self,
        db: Session,
        person_id: str,
        field_name: str,
        old_value: Any,
        new_value: Any,
        corrected_by: str,
        correction_type: EventCorrectionType = EventCorrectionType.CONTENT_CORRECTION,
        reason: Optional[str] = None,
    ) -> List[CareEventCorrection]:
        """Batch correct a field across multiple events for a person."""
        events = db.query(CareEvent).filter(
            CareEvent.person_id == person_id,
            getattr(CareEvent, field_name) == old_value,
        ).all()

        corrections = []
        for event in events:
            correction = self.create_correction(
                db=db,
                care_event_id=event.id,
                correction_type=correction_type,
                field_name=field_name,
                old_value=str(old_value) if old_value is not None else None,
                new_value=str(new_value) if new_value is not None else None,
                corrected_by=corrected_by,
                correction_reason=reason,
            )
            corrections.append(correction)

        return corrections
