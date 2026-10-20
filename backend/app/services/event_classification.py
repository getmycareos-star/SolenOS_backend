"""Event Classification & Normalization Service.

Classifies and normalizes candidate Care Events from detection results.
Implements temporal/intent classification preserving original expressions.

Architecture:
  - Rule-based classification with confidence scoring
  - Temporal resolution via Temporal Intelligence layer
  - Intent classification (current occurrence, historical reference, planned future)
  - Normalization preserving original expression
  - State impact inference and validation
"""

from __future__ import annotations

import re
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple
from enum import Enum

from sqlalchemy.orm import Session

from app.core.event_enums import (
    CareEventType,
    CareEventSubtype,
    CareEventStatus,
    CareEventSeverity,
    CareEventRelationshipType,
    CareEventGroupingType,
    EventSubjectRole,
    EventParticipantRole,
    EventMentionType,
    RecurrenceDetectionMethod,
    EventTimeGranularity,
    EventDurationType,
    EventLifecycleStage,
)
from app.core.evidence_enums import (
    EvidenceType,
    EvidenceStatus,
    NegationStatus,
    UncertaintyLevel,
    HedgeType,
    TemporalGranularity,
    TemporalAnchorType,
)
from app.models.care_event import CareEvent
from app.models.evidence import Evidence
from app.services.event_detection import EventDetectionResult
from app.services.temporal_intelligence import (
    resolve_relative_time,
    classify_temporal_status,
)


class EventClassificationResult:
    """Result of classifying an event detection candidate."""

    def __init__(self, candidate: EventDetectionResult):
        self.candidate = candidate
        self.classified_event_type: Optional[CareEventType] = None
        self.classified_subtype: Optional[CareEventSubtype] = None
        self.classified_status: CareEventStatus = CareEventStatus.UNCONFIRMED
        self.classified_severity: CareEventSeverity = CareEventSeverity.UNKNOWN
        self.classified_mention_type: EventMentionType = EventMentionType.CURRENT_OCCURRENCE
        self.confidence_event_type: Optional[float] = None
        self.confidence_status: Optional[float] = None
        self.confidence_severity: Optional[float] = None
        self.temporal_resolution_confidence: Optional[float] = None
        self.state_impacts: Optional[List[Dict[str, Any]]] = None
        self.outcome: Optional[str] = None
        self.outcome_type: Optional[str] = None
        self.outcome_confidence: Optional[float] = None
        self.outcome_time: Optional[datetime] = None
        self.duration_info: Optional[Dict[str, Any]] = None
        self.classification_notes: List[str] = []
        self.is_valid: bool = True

        # Populate from candidate
        self._classify()

    def _classify(self):
        """Run full classification pipeline."""
        self._classify_event_type()
        self._classify_subtype()
        self._classify_status()
        self._classify_severity()
        self._classify_mention_type()
        self._resolve_temporal()
        self._infer_outcome()
        self._classify_duration()
        self._infer_state_impacts()
        self._final_confidence()

    def _classify_event_type(self):
        """Classify event type with confidence scoring."""
        candidate = self.candidate

        # Primary classification from detection
        self.classified_event_type = candidate.event_type
        self.confidence_event_type = candidate.confidence.event_type or 0.7

        # Refine based on evidence type alignment
        evidence_type_to_event = {
            EvidenceType.BLOOD_PRESSURE: CareEventType.BLOOD_PRESSURE_MEASUREMENT,
            EvidenceType.WEIGHT: CareEventType.WEIGHT_MEASUREMENT,
            EvidenceType.TEMPERATURE: CareEventType.TEMPERATURE_MEASUREMENT,
            EvidenceType.GLUCOSE: CareEventType.GLUCOSE_MEASUREMENT,
            EvidenceType.MEDICATION_TAKEN: CareEventType.MEDICATION_TAKEN_EVENT,
            EvidenceType.MEDICATION_MISSED: CareEventType.MEDICATION_MISSED_EVENT,
            EvidenceType.MEDICATION_REFUSED: CareEventType.MEDICATION_REFUSED_EVENT,
            EvidenceType.MEDICATION_MENTIONED: CareEventType.MEDICATION_INSTRUCTION_EVENT,
            EvidenceType.OBSERVATION: CareEventType.FALL_EVENT,
            EvidenceType.SYMPTOM_REPORT: CareEventType.SYMPTOM_EVENT,
            EvidenceType.APPOINTMENT_EVENT: CareEventType.APPOINTMENT_EVENT,
            EvidenceType.CAREGIVER_COMMUNICATION: CareEventType.COMMUNICATION_EVENT,
        }

        # If evidence has a specific type, boost confidence if it aligns
        if candidate.evidence_ids:
            # Check each evidence item's type
            for evidence in self._load_evidence_batch(candidate.evidence_ids):
                expected_type = evidence_type_to_event.get(evidence.type)
                if expected_type and expected_type == candidate.event_type:
                    self.confidence_event_type = min(1.0, self.confidence_event_type + 0.15)
                    self.classification_notes.append(
                        f"Evidence type {evidence.type} aligns with event type {candidate.event_type}"
                    )

    def _classify_subtype(self):
        """Classify event subtype based on text analysis."""
        candidate = self.candidate
        source_text = candidate.original_expression or ""

        # Medication subtypes
        if candidate.event_type in (
            CareEventType.MEDICATION_START_EVENT,
            CareEventType.MEDICATION_STOP_EVENT,
            CareEventType.MEDICATION_MISSED_EVENT,
            CareEventType.MEDICATION_REFUSED_EVENT,
            CareEventType.MEDICATION_TAKEN_EVENT,
        ):
            text_lower = source_text.lower()
            if "started" in text_lower or "beginning" in text_lower or "initiated" in text_lower:
                self.classified_subtype = CareEventSubtype.MEDICATION_STARTED
            elif "stopped" in text_lower or "discontinued" in text_lower:
                self.classified_subtype = CareEventSubtype.MEDICATION_STOPPED
            elif "missed" in text_lower or "forgot" in text_lower:
                self.classified_subtype = CareEventSubtype.MEDICATION_MISSED
            elif "refused" in text_lower or "won't take" in text_lower:
                self.classified_subtype = CareEventSubtype.MEDICATION_REFUSED
            elif "took" in text_lower or "taken" in text_lower:
                self.classified_subtype = CareEventSubtype.MEDICATION_TAKEN

        # Appointment subtypes
        elif candidate.event_type == CareEventType.APPOINTMENT_EVENT:
            text_lower = source_text.lower()
            if "scheduled" in text_lower or "appointment" in text_lower:
                self.classified_subtype = CareEventSubtype.APPOINTMENT_SCHEDULED
            elif "completed" in text_lower or "saw" in text_lower:
                self.classified_subtype = CareEventSubtype.APPOINTMENT_COMPLETED
            elif "cancelled" in text_lower or "missed" in text_lower or "no show" in text_lower:
                self.classified_subtype = CareEventSubtype.APPOINTMENT_CANCELLED

        # Fall subtypes
        elif candidate.event_type == CareEventType.FALL_EVENT:
            text_lower = source_text.lower()
            if "witnessed" in text_lower:
                self.classified_subtype = CareEventSubtype.FALL_WITNESSED
            elif "unwitnessed" in text_lower:
                self.classified_subtype = CareEventSubtype.FALL_UNWITNESSED
            elif "reported" in text_lower:
                self.classified_subtype = CareEventSubtype.FALL_REPORTED

        # Hospitalization subtypes
        elif candidate.event_type == CareEventType.HOSPITALIZATION_EVENT:
            text_lower = source_text.lower()
            if "emergency" in text_lower or "er" in text_lower:
                self.classified_subtype = CareEventSubtype.ADMISSION_EMERGENCY
            elif "planned" in text_lower:
                self.classified_subtype = CareEventSubtype.ADMISSION_PLANNED

        # Default to candidate's subtype if set
        if self.classified_subtype is None:
            self.classified_subtype = candidate.event_subtype

    def _classify_status(self):
        """Classify event status based on temporal and linguistic cues."""
        candidate = self.candidate

        # Start with detection status
        self.classified_status = candidate.status
        self.confidence_status = candidate.confidence.status or 0.7

        source_text = candidate.original_expression or ""
        text_lower = source_text.lower()

        # Refine status based on linguistic cues
        if "scheduled" in text_lower:
            self.classified_status = CareEventStatus.SCHEDULED
            self.confidence_status = 0.9
        elif "planned" in text_lower:
            self.classified_status = CareEventStatus.PLANNED
        elif "cancelled" in text_lower or "canceled" in text_lower:
            self.classified_status = CareEventStatus.CANCELLED
            self.confidence_status = 0.9
        elif "completed" in text_lower or "done" in text_lower:
            self.classified_status = CareEventStatus.COMPLETED
            self.confidence_status = 0.85
        elif "started" in text_lower or "began" in text_lower:
            self.classified_status = CareEventStatus.IN_PROGRESS
            self.confidence_status = 0.8
        elif "ongoing" in text_lower:
            self.classified_status = CareEventStatus.ONGOING
            self.confidence_status = 0.75

        # Override for medication taken/missed/refused
        if candidate.event_type in (
            CareEventType.MEDICATION_TAKEN_EVENT,
            CareEventType.MEDICATION_MISSED_EVENT,
            CareEventType.MEDICATION_REFUSED_EVENT,
        ):
            self.classified_status = CareEventStatus.COMPLETED
            self.confidence_status = 0.85

        # Override for fall events
        if candidate.event_type == CareEventType.FALL_EVENT:
            self.classified_status = CareEventStatus.COMPLETED
            self.confidence_status = 0.95

    def _classify_severity(self):
        """Classify event severity based on evidence metadata."""
        candidate = self.candidate
        self.classified_severity = candidate.severity
        self.confidence_severity = candidate.severity_confidence or 0.7

    def _classify_mention_type(self):
        """Classify mention type: current occurrence, historical reference, planned future, etc."""
        candidate = self.candidate

        # Start with detection mention type
        self.classified_mention_type = candidate.mention_type

        source_text = candidate.original_expression or ""
        text_lower = source_text.lower()
        normalized = candidate.normalized_text or ""

        # Historical references (past tense, past dates)
        if any(w in text_lower for w in ["yesterday", "last week", "last month", "last year",
                                          "had a fall", "happened", "occurred", "experienced"]):
            self.classified_mention_type = EventMentionType.HISTORICAL_REFERENCE
        # Planned future
        elif any(w in text_lower for w in ["scheduled", "plan", "will", "going to", "appointment",
                                            "upcoming", "next week", "tomorrow"]):
            self.classified_mention_type = EventMentionType.PLANNED_FUTURE
        # Hypothetical
        elif any(w in text_lower for w in ["if", "should", "would", "could", "might", "hypothetical"]):
            if "fall" in text_lower and ("could" in text_lower or "might" in text_lower):
                self.classified_mention_type = EventMentionType.HYPOTHETICAL
        # Negative assertion (negated content)
        elif candidate.mention_type == EventMentionType.NEGATIVE_ASSERTION:
            self.classified_mention_type = EventMentionType.NEGATIVE_ASSERTION
        # Repeated mention
        elif candidate.mention_type == EventMentionType.REPEATED_MENTION:
            self.classified_mention_type = EventMentionType.REPEATED_MENTION
        # Summary assertion
        elif any(w in text_lower for w in ["summary", "overall", "in general", "typically", "usually", "often", "frequently"]):
            self.classified_mention_type = EventMentionType.SUMMARY_ASSERTION
        # Copy forward
        elif any(w in text_lower for w in ["as above", "as noted", "see above", "per above", "same as", "continue"]):
            self.classified_mention_type = EventMentionType.COPY_FORWARD
        # Historical reference (default for past events)
        elif candidate.event_time and candidate.knowledge_time and candidate.event_time < candidate.knowledge_time:
            self.classified_mention_type = EventMentionType.HISTORICAL_REFERENCE
        else:
            self.classified_mention_type = EventMentionType.CURRENT_OCCURRENCE

    def _resolve_temporal(self):
        """Resolve temporal information using Temporal Intelligence layer."""
        candidate = self.candidate

        # If we already have a resolved event_time, use it
        if candidate.event_time and candidate.event_time_granularity != EventTimeGranularity.UNKNOWN:
            self.temporal_resolution_confidence = candidate.event_time_confidence or 0.8
        else:
            self.temporal_resolution_confidence = candidate.event_time_confidence or 0.5

    def _infer_outcome(self):
        """Infer event outcome where possible."""
        candidate = self.candidate
        source_text = candidate.original_expression or ""
        text_lower = source_text.lower()

        # Appointment outcome
        if candidate.event_type == CareEventType.APPOINTMENT_EVENT:
            if "completed" in text_lower or "saw" in text_lower or "seen" in text_lower:
                self.outcome = "completed"
                self.outcome_type = "appointment_outcome"
                self.outcome_confidence = 0.9
            elif "cancelled" in text_lower or "missed" in text_lower or "no show" in text_lower:
                self.outcome = "cancelled"
                self.outcome_type = "appointment_outcome"
                self.outcome_confidence = 0.9

        # Medication outcome
        elif candidate.event_type in (
            CareEventType.MEDICATION_TAKEN_EVENT,
            CareEventType.MEDICATION_MISSED_EVENT,
            CareEventType.MEDICATION_REFUSED_EVENT,
        ):
            if candidate.event_type == CareEventType.MEDICATION_TAKEN_EVENT:
                self.outcome = "taken"
            elif candidate.event_type == CareEventType.MEDICATION_MISSED_EVENT:
                self.outcome = "missed"
            elif candidate.event_type == CareEventType.MEDICATION_REFUSED_EVENT:
                self.outcome = "refused"
            self.outcome_type = "medication_adherence_outcome"
            self.outcome_confidence = 0.85

        # Fall outcome
        elif candidate.event_type == CareEventType.FALL_EVENT:
            if "injury" in text_lower or "fracture" in text_lower or "broken" in text_lower:
                self.outcome = "with_injury"
                self.outcome_confidence = 0.7
            elif "no injury" in text_lower or "uninjured" in text_lower:
                self.outcome = "no_injury"
                self.outcome_confidence = 0.8
            else:
                self.outcome = "unknown"
                self.outcome_confidence = 0.5
            self.outcome_type = "fall_outcome"

    def _classify_duration(self):
        """Classify event duration type and extract duration values."""
        candidate = self.candidate
        source_text = candidate.original_expression or ""

        # Try to extract duration from text
        duration_match = re.search(
            r"(\d+(?:\.\d+)?)\s*(day|days|week|weeks|month|months|year|years|hour|hours|minute|minutes)",
            source_text,
            re.IGNORECASE,
        )

        if duration_match:
            value = float(duration_match.group(1))
            unit = duration_match.group(2).lower()
            self.duration_info = {
                "duration_value": value,
                "duration_unit": unit,
                "duration_type": "interval",
            }
            if "ongoing" in source_text.lower() or "currently" in source_text.lower():
                candidate.event_duration_type = EventDurationType.ONGOING
            else:
                candidate.event_duration_type = EventDurationType.INTERVAL_BASED
        else:
            # Infer duration type from event type
            if candidate.event_type in (
                CareEventType.MEDICATION_TAKEN_EVENT,
                CareEventType.MEDICATION_MISSED_EVENT,
                CareEventType.FALL_EVENT,
                CareEventType.BLOOD_PRESSURE_MEASUREMENT,
            ):
                candidate.event_duration_type = EventDurationType.INSTANTANEOUS
            elif candidate.event_type in (
                CareEventType.HOSPITALIZATION_EVENT,
                CareEventType.MEDICATION_START_EVENT,
            ):
                candidate.event_duration_type = EventDurationType.INTERVAL_BASED
            else:
                candidate.event_duration_type = EventDurationType.UNKNOWN_DURATION

    def _infer_state_impacts(self):
        """Infer state impacts for the classified event."""
        candidate = self.candidate
        source_text = candidate.original_expression or ""

        # Start with candidate's state impacts
        if candidate.state_impacts:
            self.state_impacts = candidate.state_impacts
        else:
            # Infer based on classified event type
            impacts = []

            if self.classified_event_type == CareEventType.FALL_EVENT:
                impacts.append({
                    "state_type": "mobility",
                    "impact_type": "changes",
                    "confidence": "possible",
                    "reasoning_rule": "fall_implies_mobility_change",
                })
            elif self.classified_event_type == CareEventType.HOSPITALIZATION_EVENT:
                impacts.append({
                    "state_type": "care_intensity",
                    "impact_type": "increases",
                    "confidence": "supported",
                    "reasoning_rule": "hospitalization_implies_care_intensity",
                })
            elif self.classified_event_type in (
                CareEventType.MEDICATION_START_EVENT,
                CareEventType.MEDICATION_STOP_EVENT,
                CareEventType.MEDICATION_DOSE_CHANGE_EVENT,
            ):
                impacts.append({
                    "state_type": "medication_regimen",
                    "impact_type": "changes",
                    "confidence": "supported",
                    "reasoning_rule": "med_change_implies_regimen_change",
                })
            elif self.classified_event_type == CareEventType.MEDICATION_MISSED_EVENT:
                impacts.append({
                    "state_type": "medication_adherence",
                    "impact_type": "decreases",
                    "confidence": "supported",
                    "reasoning_rule": "missed_dose_implies_adherence_decrease",
                })
            elif self.classified_event_type == CareEventType.MEDICATION_TAKEN_EVENT:
                impacts.append({
                    "state_type": "medication_adherence",
                    "impact_type": "supports",
                    "confidence": "supported",
                    "reasoning_rule": "med_taken_supports_adherence",
                })
            elif self.classified_event_type == CareEventType.OBSERVATION_EVENT:
                if any(w in source_text.lower() for w in EventClassificationService.COGNITIVE_KEYWORDS):
                    impacts.append({
                        "state_type": "cognitive_status",
                        "impact_type": "changes",
                        "confidence": "possible",
                        "reasoning_rule": "cognitive_observation_implies_cognitive_change",
                    })
                elif any(w in source_text.lower() for w in EventClassificationService.BEHAVIORAL_KEYWORDS):
                    impacts.append({
                        "state_type": "behavioral_status",
                        "impact_type": "changes",
                        "confidence": "possible",
                        "reasoning_rule": "behavioral_observation_implies_behavior_change",
                    })

            self.state_impacts = impacts if impacts else None

    def _final_confidence(self):
        """Calculate final confidence dimensions for the classified event."""
        candidate = self.candidate

        overall_dims = [
            candidate.confidence.event_detection,
            self.confidence_event_type,
            candidate.confidence.subject,
            self.temporal_resolution_confidence,
            candidate.confidence.identity,
            self.confidence_severity,
            self.confidence_status,
        ]
        valid_dims = [d for d in overall_dims if d is not None]
        overall = sum(valid_dims) / len(valid_dims) if valid_dims else 0.5

        # Update candidate confidence with classified values
        candidate.confidence.event_type = self.confidence_event_type
        candidate.confidence.status = self.confidence_status
        candidate.confidence.severity = self.confidence_severity
        candidate.confidence.temporal = self.temporal_resolution_confidence
        candidate.confidence.overall = round(overall, 2)

    def _load_evidence_batch(self, evidence_ids: List[str]):
        """Load evidence items for batch processing.

        Classification must not depend on a live database connection: the
        event type is already known from detection, and evidence alignment
        only *boosts* confidence. If the evidence rows are not present (for
        example in a unit test that constructs a candidate directly), the
        classification still completes with its base confidence.
        """
        if not evidence_ids:
            return []
        from app.core.database import SessionLocal
        db = SessionLocal()
        try:
            return db.query(Evidence).filter(Evidence.id.in_(evidence_ids)).all()
        except Exception:
            return []
        finally:
            db.close()

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for event creation."""
        c = self.candidate
        return {
            "person_id": c.person_id,
            "subject_role": c.subject_role.value if isinstance(c.subject_role, Enum) else c.subject_role,
            "subject_id": c.subject_id,
            "subject_label": c.subject_label,
            "entity_resolution_status": c.entity_resolution_status,
            "entity_resolution_confidence": c.entity_resolution_confidence,
            "event_type": self.classified_event_type.value if self.classified_event_type else c.event_type.value if isinstance(c.event_type, Enum) else c.event_type,
            "event_subtype": self.classified_subtype.value if self.classified_subtype else (c.event_subtype.value if c.event_subtype and isinstance(c.event_subtype, Enum) else c.event_subtype),
            "event_category": c.event_category,
            "title": c.title,
            "description": c.description or self.candidate.normalized_text,
            "original_expression": c.original_expression,
            "normalized_text": c.normalized_text,
            "participants": c.participants,
            "primary_caregiver_id": c.primary_caregiver_id,
            "primary_provider_id": c.primary_provider_id,
            "facility_id": c.facility_id,
            "location": c.location,
            "location_provenance": c.location_provenance,
            "evidence_ids": c.evidence_ids,
            "primary_evidence_id": c.primary_evidence_id,
            "source_ids": c.source_ids,
            "extraction_method": c.extraction_method,
            "extraction_version": c.extraction_version,
            "extraction_confidence": c.extraction_confidence,
            "confidence": c.confidence.to_dict(),
            "event_time": c.event_time.isoformat() if c.event_time else None,
            "event_time_granularity": c.event_time_granularity.value if c.event_time_granularity and isinstance(c.event_time_granularity, Enum) else c.event_time_granularity,
            "event_time_text": c.event_time_text,
            "event_time_anchor": c.event_time_anchor,
            "event_time_lower_bound": c.event_time_lower_bound.isoformat() if c.event_time_lower_bound else None,
            "event_time_upper_bound": c.event_time_upper_bound.isoformat() if c.event_time_upper_bound else None,
            "event_time_confidence": c.event_time_confidence,
            "knowledge_time": c.knowledge_time.isoformat() if c.knowledge_time else None,
            "documentation_time": c.documentation_time.isoformat() if c.documentation_time else None,
            "authoring_time": c.authoring_time.isoformat() if c.authoring_time else None,
            "event_duration": c.event_duration,
            "event_duration_type": c.event_duration_type.value if c.event_duration_type and isinstance(c.event_duration_type, Enum) else c.event_duration_type,
            "event_duration_value": c.event_duration_value,
            "event_duration_unit": c.event_duration_unit,
            "event_duration_confidence": c.event_duration_confidence,
            "event_duration_is_explicit": c.event_duration_is_explicit,
            "status": self.classified_status.value if isinstance(self.classified_status, Enum) else self.classified_status,
            "status_reason": c.status_reason,
            "status_confidence": self.confidence_status,
            "severity": self.classified_severity.value if isinstance(self.classified_severity, Enum) else self.classified_severity,
            "severity_confidence": self.confidence_severity,
            "severity_details": c.severity_details,
            "outcome": self.outcome,
            "outcome_type": self.outcome_type,
            "outcome_confidence": self.outcome_confidence,
            "outcome_evidence_ids": c.outcome_evidence_ids,
            "outcome_time": c.outcome_time.isoformat() if c.outcome_time else None,
            "outcome_time_granularity": c.outcome_time_granularity.value if c.outcome_time_granularity and isinstance(c.outcome_time_granularity, Enum) else c.outcome_time_granularity,
            "mention_type": self.classified_mention_type.value if isinstance(self.classified_mention_type, Enum) else self.classified_mention_type,
            "mention_source_event_id": c.mention_source_event_id,
            "lifecycle_stage": c.lifecycle_stage.value if isinstance(c.lifecycle_stage, Enum) else c.lifecycle_stage,
            "superseded_by_event_id": c.superseded_by_event_id,
            "recurrence_group_id": c.recurrence_group_id,
            "recurrence_sequence": c.recurrence_sequence,
            "recurrence_detection_method": c.recurrence_detection_method.value if c.recurrence_detection_method and isinstance(c.recurrence_detection_method, Enum) else c.recurrence_detection_method,
            "recurrence_confidence": c.recurrence_confidence,
            "is_recurrence_representative": c.is_recurrence_representative,
            "group_id": c.group_id,
            "group_type": c.group_type.value if c.group_type and isinstance(c.group_type, Enum) else c.group_type,
            "group_role": c.group_role,
            "state_impacts": self.state_impacts,
            "state_impact_confidence": c.state_impact_confidence,
            "situation_id": c.situation_id,
            "situation_role": c.situation_role,
            "change_signals": c.change_signals,
            "watch_signals": c.watch_signals,
            "unresolved_questions": c.unresolved_questions,
            "required_fields_present": c.required_fields_present,
            "missing_required_fields": c.missing_required_fields,
            "validation_status": c.validation_status,
            "validation_errors": c.validation_errors,
            "processing_version": c.processing_version,
            "created_by_caregiver_id": c.created_by_caregiver_id,
            "quality_gate_passed": c.quality_gate_passed,
            "quality_gate_details": c.quality_gate_details,
        }


class EventClassificationService:
    """Classifies and normalizes candidate Care Events.

    This service sits downstream of detection and upstream of persistence.
    It implements temporal/intent classification while preserving original
    expressions. Never writes to database directly.
    """

    # Shared keyword lists for classification
    COGNITIVE_KEYWORDS = ["confused", "confusion", "disoriented", "forgetful",
                          "forgetting", "memory", "dementia", "alzheimer", "deterioration"]
    MOBILITY_KEYWORDS = ["walk", "walking", "mobility", "balance", "unsteady", "gait"]
    BEHAVIORAL_KEYWORDS = ["wandering", "agitated", "agitation", "restless", "withdrawn"]
    SLEEP_KEYWORDS = ["sleep", "slept", "insomnia", "awake", "woke"]
    APPETITE_KEYWORDS = ["appetite", "eat", "eating", "hungry", "nausea", "vomiting"]

    # Event type to category mapping
    EVENT_TYPE_CATEGORIES: Dict[CareEventType, str] = {
        CareEventType.FALL_EVENT: "falls",
        CareEventType.NEAR_FALL_EVENT: "falls",
        CareEventType.MEDICATION_TAKEN_EVENT: "medication",
        CareEventType.MEDICATION_START_EVENT: "medication",
        CareEventType.MEDICATION_STOP_EVENT: "medication",
        CareEventType.MEDICATION_MISSED_EVENT: "medication",
        CareEventType.MEDICATION_REFUSED_EVENT: "medication",
        CareEventType.MEDICATION_DOSE_CHANGE_EVENT: "medication",
        CareEventType.MEDICATION_CHANGE_EVENT: "medication",
        CareEventType.BLOOD_PRESSURE_MEASUREMENT: "measurement",
        CareEventType.WEIGHT_MEASUREMENT: "measurement",
        CareEventType.TEMPERATURE_MEASUREMENT: "measurement",
        CareEventType.GLUCOSE_MEASUREMENT: "measurement",
        CareEventType.APPOINTMENT_EVENT: "appointment",
        CareEventType.HOSPITALIZATION_EVENT: "hospitalization",
        CareEventType.SYMPTOM_EVENT: "symptom",
        CareEventType.OBSERVATION_EVENT: "observation",
        CareEventType.COGNITIVE_OBSERVATION: "observation",
        CareEventType.MOBILITY_OBSERVATION: "observation",
        CareEventType.SLEEP_OBSERVATION: "observation",
        CareEventType.APPETITE_OBSERVATION: "observation",
        CareEventType.BEHAVIOURAL_OBSERVATION: "observation",
        CareEventType.FUNCTIONAL_OBSERVATION: "observation",
        CareEventType.COMMUNICATION_EVENT: "communication",
        CareEventType.CAREGIVER_COMMUNICATION_EVENT: "communication",
        CareEventType.PROVIDER_COMMUNICATION_EVENT: "communication",
    }

    # Valid status transitions
    VALID_STATUS_TRANSITIONS: Dict[CareEventStatus, List[CareEventStatus]] = {
        CareEventStatus.PLANNED: [CareEventStatus.SCHEDULED, CareEventStatus.CANCELLED, CareEventStatus.IN_PROGRESS],
        CareEventStatus.SCHEDULED: [CareEventStatus.IN_PROGRESS, CareEventStatus.CANCELLED, CareEventStatus.NO_SHOW],
        CareEventStatus.TENTATIVE: [CareEventStatus.SCHEDULED, CareEventStatus.CANCELLED],
        CareEventStatus.IN_PROGRESS: [CareEventStatus.COMPLETED, CareEventStatus.ONGOING, CareEventStatus.CANCELLED, CareEventStatus.FAILED],
        CareEventStatus.ONGOING: [CareEventStatus.COMPLETED, CareEventStatus.RESOLVED, CareEventStatus.CONTESTED],
        CareEventStatus.COMPLETED: [CareEventStatus.RESOLVED, CareEventStatus.SUPERSEDED],
        CareEventStatus.RESOLVED: [CareEventStatus.SUPERSEDED],
        CareEventStatus.CANCELLED: [],
        CareEventStatus.FAILED: [],
        CareEventStatus.NO_SHOW: [],
        CareEventStatus.UNRESOLVED: [CareEventStatus.RESOLVED, CareEventStatus.CONTESTED],
        CareEventStatus.UNCONFIRMED: [CareEventStatus.COMPLETED, CareEventStatus.INVALIDATED],
        CareEventStatus.CONTESTED: [CareEventStatus.RESOLVED, CareEventStatus.COMPLETED, CareEventStatus.INVALIDATED],
        CareEventStatus.SUPERSEDED: [],
        CareEventStatus.INVALIDATED: [],
        CareEventStatus.HISTORICAL: [],
        CareEventStatus.ARCHIVED: [],
    }

    def __init__(self):
        pass

    def classify_candidate(self, candidate: EventDetectionResult) -> EventClassificationResult:
        """Classify a single detection candidate.

        Does NOT persist to database. Returns a classification result
        that can be used to create a CareEvent.
        """
        result = EventClassificationResult(candidate)
        return result

    def classify_candidates(
        self,
        candidates: List[EventDetectionResult],
    ) -> List[EventClassificationResult]:
        """Classify multiple detection candidates."""
        return [self.classify_candidate(c) for c in candidates]

    def validate_classification(
        self,
        classification: EventClassificationResult,
        db: Session,
    ) -> bool:
        """Validate a classification result against domain constraints."""
        c = classification.candidate

        # Check required fields
        if classification.is_valid and not c.required_fields_present:
            classification.is_valid = False
        if not c.person_id:
            classification.is_valid = False
        if not c.event_type:
            classification.is_valid = False
        if not c.evidence_ids:
            classification.is_valid = False

        # Validate event type exists
        if c.event_type not in CareEventType:
            classification.is_valid = False

        # Validate status transition (if updating existing event)
        if c.lifecycle_stage != EventLifecycleStage.DETECTED:
            # Check if status is valid transition from current state
            pass

        return classification.is_valid

    def normalize_event(
        self,
        classification: EventClassificationResult,
        db: Session,
    ) -> Dict[str, Any]:
        """Normalize a classified event into creation-ready data.

        Preserves original expression while adding structured normalization.
        """
        result = classification.to_dict()

        # Add normalization details
        from app.core.event_enums import EventDurationType, EventTimeGranularity

        # Extract measurement values from text if applicable
        source_text = classification.candidate.original_expression
        if classification.classified_event_type == CareEventType.BLOOD_PRESSURE_MEASUREMENT:
            bp_match = re.search(r"(\d+)\s*/\s*(\d+)", source_text)
            if bp_match:
                result["normalized_text"] = f"BP {bp_match.group(1)}/{bp_match.group(2)} mmHg"
                result["normalized_details"] = {
                    "systolic": int(bp_match.group(1)),
                    "diastolic": int(bp_match.group(2)),
                }
        elif classification.classified_event_type == CareEventType.GLUCOSE_MEASUREMENT:
            glucose_match = re.search(r"(\d+(?:\.\d+)?)\s*(mg/dl|mmol/l)", source_text, re.IGNORECASE)
            if glucose_match:
                result["normalized_text"] = f"Glucose {glucose_match.group(1)} {glucose_match.group(2)}"
                result["normalized_details"] = {
                    "value": float(glucose_match.group(1)),
                    "unit": glucose_match.group(2),
                }
        elif classification.classified_event_type == CareEventType.WEIGHT_MEASUREMENT:
            weight_match = re.search(r"(\d+(?:\.\d+)?)\s*(kg|lbs?|pounds?)", source_text, re.IGNORECASE)
            if weight_match:
                result["normalized_text"] = f"Weight {weight_match.group(1)} {weight_match.group(2)}"
                result["normalized_details"] = {
                    "value": float(weight_match.group(1)),
                    "unit": weight_match.group(2),
                }

        return result

    def get_event_category(self, event_type: CareEventType) -> Optional[str]:
        """Get the category for an event type."""
        return self.EVENT_TYPE_CATEGORIES.get(event_type)

    def is_valid_status_transition(
        self,
        from_status: CareEventStatus,
        to_status: CareEventStatus,
    ) -> bool:
        """Check if a status transition is valid."""
        valid_transitions = self.VALID_STATUS_TRANSITIONS.get(from_status, [])
        return to_status in valid_transitions
