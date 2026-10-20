"""Event Detection Engine - Transforms Evidence into candidate Care Events.

This service implements detection patterns that propose candidate Care Events
from evidence items. It NEVER writes directly - candidates are returned for
classification, deduplication, and user review.

Architecture:
  - Rule-based pattern detection for common event types
  - LLM candidate proposal for complex or ambiguous evidence
  - Strict schema validation, identity validation, evidence validation,
    temporal validation, ontology validation

Consumes: Evidence model, Evidence relationships, TemporalFact, TemporalRelations
Produces: List[CareEventCandidate] for classification pipeline
"""

from __future__ import annotations

import re
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.event_enums import (
    CareEventType,
    CareEventSubtype,
    CareEventStatus,
    CareEventSeverity,
    CareEventRelationshipType,
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
from app.models.evidence import Evidence, EvidenceRelationship, EvidenceVersion
from app.models.temporal import TemporalFact, TemporalRelation, TemporalReferenceFrame
from app.models.care_event import CareEvent, CareEventMentionDeduplication


@dataclass
class EventDetectionConfidenceDimensions:
    """Multi-dimensional confidence for event detection. Requirement 35."""
    event_detection: Optional[float] = None
    event_type: Optional[float] = None
    subject: Optional[float] = None
    temporal: Optional[float] = None
    identity: Optional[float] = None
    severity: Optional[float] = None
    status: Optional[float] = None
    outcome: Optional[float] = None
    overall: Optional[float] = None

    def to_dict(self) -> Dict[str, Optional[float]]:
        return {
            "event_detection": self.event_detection,
            "event_type": self.event_type,
            "subject": self.subject,
            "temporal": self.temporal,
            "identity": self.identity,
            "severity": self.severity,
            "status": self.status,
            "outcome": self.outcome,
            "overall": self.overall,
        }


@dataclass
class EventDetectionResult:
    """Result of detecting a single care event from evidence.

    Detection produces a candidate - it does not write to the database.
    Classification, deduplication, and lifecycle management handle persistence.
    """
    event_type: CareEventType
    event_subtype: Optional[CareEventSubtype] = None
    event_category: Optional[str] = None
    title: str = ""
    description: Optional[str] = None
    original_expression: str = ""
    normalized_text: Optional[str] = None
    person_id: str = ""
    subject_role: EventSubjectRole = EventSubjectRole.CARE_RECIPIENT
    subject_id: Optional[str] = None
    subject_label: Optional[str] = None
    entity_resolution_status: str = "resolved"
    entity_resolution_confidence: Optional[float] = None
    participants: List[Dict[str, Any]] = field(default_factory=list)
    primary_caregiver_id: Optional[str] = None
    primary_provider_id: Optional[str] = None
    facility_id: Optional[str] = None
    location: Optional[str] = None
    location_provenance: Optional[str] = None
    evidence_ids: List[str] = field(default_factory=list)
    primary_evidence_id: Optional[str] = None
    source_ids: List[str] = field(default_factory=list)
    extraction_method: Optional[str] = None
    extraction_version: Optional[str] = None
    extraction_confidence: Optional[float] = None
    confidence: EventDetectionConfidenceDimensions = field(default_factory=EventDetectionConfidenceDimensions)
    # Temporal
    event_time: Optional[datetime] = None
    event_time_granularity: Optional[EventTimeGranularity] = None
    event_time_text: Optional[str] = None
    event_time_anchor: Optional[str] = None
    event_time_lower_bound: Optional[datetime] = None
    event_time_upper_bound: Optional[datetime] = None
    event_time_confidence: Optional[float] = None
    knowledge_time: Optional[datetime] = None
    documentation_time: Optional[datetime] = None
    authoring_time: Optional[datetime] = None
    event_duration: Optional[str] = None
    event_duration_type: Optional[EventDurationType] = None
    event_duration_value: Optional[float] = None
    event_duration_unit: Optional[str] = None
    event_duration_confidence: Optional[float] = None
    event_duration_is_explicit: bool = False
    # Status, Severity, Outcome
    status: CareEventStatus = CareEventStatus.UNCONFIRMED
    status_reason: Optional[str] = None
    status_confidence: Optional[float] = None
    severity: CareEventSeverity = CareEventSeverity.UNKNOWN
    severity_confidence: Optional[float] = None
    severity_details: Optional[str] = None
    outcome: Optional[str] = None
    outcome_type: Optional[str] = None
    outcome_confidence: Optional[float] = None
    outcome_evidence_ids: List[str] = field(default_factory=list)
    outcome_time: Optional[datetime] = None
    outcome_time_granularity: Optional[EventTimeGranularity] = None
    # Mention classification
    mention_type: EventMentionType = EventMentionType.CURRENT_OCCURRENCE
    mention_source_event_id: Optional[str] = None
    # Lifecycle
    lifecycle_stage: EventLifecycleStage = EventLifecycleStage.DETECTED
    superseded_by_event_id: Optional[str] = None
    # Processing
    processing_version: Optional[str] = None
    created_by_caregiver_id: Optional[str] = None
    # Quality gate
    quality_gate_passed: bool = False
    quality_gate_details: Optional[Dict[str, Any]] = None
     # Validation
    required_fields_present: bool = True
    missing_required_fields: List[str] = field(default_factory=list)
    validation_status: str = "pending"
    validation_errors: List[str] = field(default_factory=list)
    # State impacts
    state_impacts: Optional[List[Dict[str, Any]]] = None

    def is_valid(self) -> bool:
        """Check if this detection result passes all validation."""
        return (
            self.validation_status in ("pending", "valid")
            and self.required_fields_present
            and len(self.validation_errors) == 0
            and self.quality_gate_passed
        )


class EventDetectionEngine:
    """Transforms Evidence into candidate Care Events via rule-based patterns.

    Key principles:
    - Detection never writes to database; produces candidates only
    - Each candidate references provenance to source evidence
    - Multi-dimensional confidence per Requirement 35
    - Full validation pipeline: schema, identity, evidence, temporal, ontology
    """

    # Pattern definitions: event_type -> list of (pattern, subtype, metadata)
    # Each pattern: (regex_string, event_subtype, severity, status, detection_confidence)

    TYPE_PATTERNS: Dict[CareEventType, List[Tuple[str, Optional[str], Optional[str], Optional[str], float]]] = {
        CareEventType.MEDICATION_TAKEN_EVENT: [
            (r"\b(took|taken|gave|given|administered|received)\s+([\w\s]+?)\b", None, None, None, 0.85),
            (r"\btook\s+(?:her|him|the)?\s*([\w\s]+?)(?:\s+dose|\s+of|\s+mg|\s+at|\s+and|\s*$)", None, None, None, 0.8),
        ],
        CareEventType.MEDICATION_MISSED_EVENT: [
            (r"\b(missed|forgot|didn't take|did not take|skipped|omitted)\s+(?:my\s+)?(medication|medicine|dose|pill|tablet)\b", None, None, None, 0.90),
            (r"\b(?:I forgot|I missed)\s+(to take)?\s*([\w\s]+?)(?:\s+today|\s+this morning|\s+this evening|\s*$)", None, None, None, 0.85),
        ],
        CareEventType.MEDICATION_REFUSED_EVENT: [
            (r"\b(refused|won't take|will not take|declined)\s+(?:my\s+)?(medication|medicine|pill|drug)\b", None, None, None, 0.90),
            (r"\b(?:I refused|declined)\s+([\w\s]+?)(?:\s+today|\s*$)", None, None, None, 0.80),
        ],
        CareEventType.MEDICATION_START_EVENT: [
            (r"\b(started|beginning|beginning new|initiated)\s+(?:taking|treatment with|on)\s+([\w\s]+?)\b", None, None, CareEventStatus.PLANNED, 0.85),
            (r"\b(prescribed|new prescription for)\s+([\w\s]+?)\b", None, None, CareEventStatus.PLANNED, 0.80),
        ],
        CareEventType.MEDICATION_STOP_EVENT: [
            (r"\b(stopped|discontinued|no longer take)\s+(?:taking)?\s*([\w\s]+?)\b", None, None, CareEventStatus.COMPLETED, 0.85),
        ],
        CareEventType.MEDICATION_DOSE_CHANGE_EVENT: [
            (r"\b(increased|decreased|raised|lowered|doubled|halved)\s+[dD]ose\s+of\s+([\w\s]+?)\b", None, None, None, 0.80),
            (r"\b(dosage|dose)\s+(?:was|is|changed to)\s+(\d+(?:\.\d+)?)\s*(mg|mcg|ml)\b", None, None, None, 0.75),
        ],
        CareEventType.FALL_EVENT: [
            (r"\b(fell|fall|fallen|take a fall|took a fall)\b", None, None, CareEventStatus.COMPLETED, 0.95),
        ],
        CareEventType.NEAR_FALL_EVENT: [
            (r"\b(nearly fell|almost fell|near fall|near-fall|close call|stumbled but caught)\b", None, None, CareEventStatus.COMPLETED, 0.90),
        ],
        CareEventType.HOSPITALIZATION_EVENT: [
            (r"\b(admitted| hospitalized|inpatient|stayed in hospital|went to hospital|ER visit|emergency room)\b", None, None, CareEventStatus.COMPLETED, 0.90),
        ],
        CareEventType.APPOINTMENT_EVENT: [
            (r"\b(appointment|visit|see|seen|saw|met with|consultation)\s+(?:doctor|dr\.|physician|nurse|specialist|gp)\b", None, None, None, 0.85),
        ],
        CareEventType.SYMPTOM_EVENT: [
            (r"\b(pain|ache|hurt|sore|symptom|discomfort|burning|tingling|numbness)\b", None, None, None, 0.80),
        ],
        CareEventType.OBSERVATION_EVENT: [
            (r"\b(confused|confusion|disoriented|forgetful|forgetting|wandering|agitation|agitated|restless|withdrawn|quiet)\b", None, None, None, 0.75),
        ],
        CareEventType.BLOOD_PRESSURE_MEASUREMENT: [
            (r"\b(bp|blood pressure)\s*(?:was|is|of|:)?\s*(\d+)\s*/\s*(\d+)\b", None, None, None, 0.95),
            (r"\b(\d+)\s*/\s*(\d+)\s*(?:mmhg|mmhg)\b", None, None, None, 0.90),
        ],
        CareEventType.WEIGHT_MEASUREMENT: [
            (r"\b(weight|weighs|weighed)\s*(?:was|is|:)?\s*(\d+(?:\.\d+)?)\s*(?:kg|lbs?|pounds?|kilograms?)\b", None, None, None, 0.95),
        ],
        CareEventType.TEMPERATURE_MEASUREMENT: [
            (r"\b(temp|temperature)\s*(?:was|is|:)?\s*(\d+(?:\.\d+)?)\s*(?:c|f|celsius|fahrenheit|°c|°f)\b", None, None, None, 0.95),
        ],
        CareEventType.GLUCOSE_MEASUREMENT: [
            (r"\b(glucose|blood sugar|sugar)\s*(?:was|is|:)?\s*(\d+(?:\.\d+)?)\s*(?:mg/dl|mmol/l)\b", None, None, None, 0.95),
        ],
        CareEventType.MEDICATION_INSTRUCTION_EVENT: [
            (r"\b(take|taking|took|should take|must take|need to take)\s+([\w\s]+?)(?:\s+daily|\s+twice|\s+once|\s+at bedtime|\s*$)", None, None, CareEventStatus.PLANNED, 0.80),
        ],
    }

    # Medication name extraction patterns
    MEDICATION_NAME_PATTERNS = [
        r"\b(?:took|taken|gave|given|started|stopped|taking|prescribed|on|of)\s+([\w\-]+(?:\s+[\w\-]+)*)\b",
        r"\b([\w\-]+(?:\s+[\w\-]+)*)\s+(?:5mg|10mg|20mg|25mg|50mg|100mg|200mg|tablet|pill|dose|mg|mcg|ml)\b",
        r"\b(?:metformin|lisinopril|atorvastatin|simvastatin|levothyroxine|omeprazole|albuterol|insulin|aspirin|warfarin|lisinopril|metoprolol|atorvastatin|simvastatin)\b",
    ]

    # Observation type patterns for subtype classification
    COGNITIVE_KEYWORDS = ["confused", "confusion", "disoriented", "forgetful", "forgetting", "memory", "dementia", "alzheimer"]
    MOBILITY_KEYWORDS = ["walk", "walking", "mobility", "balance", "unsteady", "gait", "fall"]
    BEHAVIORAL_KEYWORDS = ["wandering", "agitated", "agitation", "restless", "withdrawn", "quiet", "pacing"]
    SLEEP_KEYWORDS = ["sleep", "slept", "insomnia", "awake", "woke", "night"]
    APPETITE_KEYWORDS = ["appetite", "eat", "eating", "hungry", "nausea", "vomiting", "lost appetite"]

    def __init__(self):
        self._compiled_patterns: Dict[CareEventType, List[Tuple]] = {}
        self._compile_patterns()

    def _compile_patterns(self):
        """Pre-compile regex patterns for performance."""
        for event_type, patterns in self.TYPE_PATTERNS.items():
            compiled = []
            for pattern, subtype, severity, status, conf in patterns:
                compiled.append((
                    re.compile(pattern, re.IGNORECASE),
                    subtype,
                    severity,
                    status,
                    conf,
                ))
            self._compiled_patterns[event_type] = compiled

    def detect_events_from_evidence(
        self,
        db: Session,
        evidence_ids: Optional[List[str]] = None,
        person_id: Optional[str] = None,
    ) -> List[EventDetectionResult]:
        """Detect candidate care events from evidence items.

        Either evidence_ids or person_id must be provided.
        If person_id is provided, all active evidence for that person is used.
        """
        evidence_items = self._load_evidence(db, evidence_ids, person_id)
        if not evidence_items:
            return []

        candidates: List[EventDetectionResult] = []

        for evidence in evidence_items:
            if evidence.status != EvidenceStatus.ACTIVE:
                continue

            # Skip evidence that's negation but still process it (for NEGATIVE_ASSERTION mention type)
            detected = self._detect_from_single_evidence(evidence)
            if detected:
                candidates.extend(detected)

        # Cross-evidence detection (e.g., medication taken + BP measurement -> med_effectiveness)
        cross_detected = self._detect_cross_evidence(db, evidence_items)
        candidates.extend(cross_detected)

        # Validate all candidates
        validated = []
        for candidate in candidates:
            self._validate_candidate(db, candidate, evidence_items)
            if candidate.quality_gate_passed:
                validated.append(candidate)

        return validated

    def _load_evidence(
        self,
        db: Session,
        evidence_ids: Optional[List[str]],
        person_id: Optional[str],
    ) -> List[Evidence]:
        """Load evidence items for detection."""
        if evidence_ids:
            stmt = db.query(Evidence).filter(Evidence.id.in_(evidence_ids))
            return stmt.all()
        elif person_id:
            return (
                db.query(Evidence)
                .filter(Evidence.subject_id == person_id, Evidence.status == EvidenceStatus.ACTIVE)
                .all()
            )
        return []

    def _detect_from_single_evidence(self, evidence: Evidence) -> List[EventDetectionResult]:
        """Detect candidate events from a single evidence item."""
        candidates: List[EventDetectionResult] = []

        # Determine which detection patterns to apply based on evidence type
        source_text = evidence.source_text or ""
        normalized_text = evidence.normalized_text or ""

        # Try patterns for all event types
        for event_type, patterns in self._compiled_patterns.items():
            for pattern, subtype, severity, status, conf in patterns:
                match = pattern.search(source_text) or pattern.search(normalized_text)
                if match:
                    candidate = self._build_candidate(
                        evidence=evidence,
                        event_type=event_type,
                        match=match,
                        source_text=source_text,
                        detection_confidence=conf,
                    )
                    if candidate:
                        candidates.append(candidate)
                    break  # One pattern match per type is enough

        # Also detect based on evidence type mapping
        type_based = self._detect_from_evidence_type(evidence)
        if type_based:
            candidates.extend(type_based)

        return candidates

    def _detect_from_evidence_type(self, evidence: Evidence) -> List[EventDetectionResult]:
        """Map evidence type directly to event type."""
        candidates: List[EventDetectionResult] = []
        source_text = evidence.source_text or ""

        evidence_to_event_map: Dict[EvidenceType, CareEventType] = {
            EvidenceType.BLOOD_PRESSURE: CareEventType.BLOOD_PRESSURE_MEASUREMENT,
            EvidenceType.WEIGHT: CareEventType.WEIGHT_MEASUREMENT,
            EvidenceType.TEMPERATURE: CareEventType.TEMPERATURE_MEASUREMENT,
            EvidenceType.GLUCOSE: CareEventType.GLUCOSE_MEASUREMENT,
            EvidenceType.MEDICATION_TAKEN: CareEventType.MEDICATION_TAKEN_EVENT,
            EvidenceType.MEDICATION_MISSED: CareEventType.MEDICATION_MISSED_EVENT,
            EvidenceType.MEDICATION_REFUSED: CareEventType.MEDICATION_REFUSED_EVENT,
            EvidenceType.MEDICATION_MENTIONED: CareEventType.MEDICATION_INSTRUCTION_EVENT,
            EvidenceType.OBSERVATION: CareEventType.FALL_EVENT,
            EvidenceType.COGNITIVE_OBSERVATION: CareEventType.OBSERVATION_EVENT,
            EvidenceType.MOBILITY_OBSERVATION: CareEventType.OBSERVATION_EVENT,
            EvidenceType.BEHAVIOURAL_OBSERVATION: CareEventType.OBSERVATION_EVENT,
            EvidenceType.SYMPTOM_REPORT: CareEventType.SYMPTOM_EVENT,
            EvidenceType.APPOINTMENT_EVENT: CareEventType.APPOINTMENT_EVENT,
            EvidenceType.CAREGIVER_COMMUNICATION: CareEventType.COMMUNICATION_EVENT,
        }

        event_type = evidence_to_event_map.get(evidence.type)
        if event_type:
            candidate = self._build_candidate_from_evidence_type(
                evidence=evidence,
                event_type=event_type,
            )
            if candidate:
                candidates.append(candidate)

        return candidates

    def _detect_cross_evidence(
        self,
        db: Session,
        evidence_items: List[Evidence],
    ) -> List[EventDetectionResult]:
        """Detect events that require cross-evidence analysis."""
        candidates: List[EventDetectionResult] = []

        # Check for medication administration followed by vital sign changes
        medication_evidence = [
            e for e in evidence_items
            if e.type in (
                EvidenceType.MEDICATION_TAKEN,
                EvidenceType.MEDICATION_MENTIONED,
                EvidenceType.MEDICATION_MISSED,
                EvidenceType.MEDICATION_REFUSED,
            )
        ]

        vital_evidence = [
            e for e in evidence_items
            if e.type in (
                EvidenceType.BLOOD_PRESSURE,
                EvidenceType.GLUCOSE,
                EvidenceType.TEMPERATURE,
                EvidenceType.WEIGHT,
            )
        ]

        # Check for relationships between medication and vital evidence
        for med_ev in medication_evidence:
            for vital_ev in vital_evidence:
                # Check if they're temporally close
                if self._events_temporally_related(med_ev, vital_ev):
                    # This could be a medication_vital_trend event
                    # (implementation would go here)
                    pass

        # Check for appointment + medication correlation (picked up prescription)
        appointment_evidence = [e for e in evidence_items if e.type == EvidenceType.APPOINTMENT_EVENT]
        if medication_evidence and appointment_evidence:
            # Could detect medication pickup after appointment
            for med_ev in medication_evidence:
                for apt_ev in appointment_evidence:
                    if self._events_temporally_related(med_ev, apt_ev, max_gap_hours=48):
                        candidate = self._build_cross_evidence_candidate(
                            primary_evidence=med_ev,
                            secondary_evidence=apt_ev,
                            event_type=CareEventType.MEDICATION_TAKEN_EVENT,
                            confidence=0.75,
                            description="Medication taken following appointment",
                        )
                        if candidate:
                            candidates.append(candidate)

        return candidates

    def _build_candidate(
        self,
        evidence: Evidence,
        event_type: CareEventType,
        match: re.Match,
        source_text: str,
        detection_confidence: float,
    ) -> Optional[EventDetectionResult]:
        """Build a detection candidate from a regex match."""
        # Determine subject
        person_id = evidence.subject_id
        if not person_id:
            return None

        # Extract temporal info
        event_time = evidence.event_time
        knowledge_time = evidence.observation_time or evidence.processing_timestamp
        documentation_time = evidence.document_time or evidence.authoring_time
        authoring_time = evidence.authoring_time

        # Build temporal info
        event_time_granularity = EventTimeGranularity.UNKNOWN
        if evidence.event_time_granularity:
            event_time_granularity = self._map_temporal_granularity(evidence.event_time_granularity)

        # Determine mention type based on negation and evidence properties
        mention_type = EventMentionType.CURRENT_OCCURRENCE
        if evidence.negation_status == NegationStatus.NEGATED:
            mention_type = EventMentionType.NEGATIVE_ASSERTION
        elif evidence.uncertainty_level == UncertaintyLevel.EXPLICITLY_UNCERTAIN:
            mention_type = EventMentionType.HISTORICAL_REFERENCE

        # Build participants list
        participants = []
        if evidence.observer_id:
            participants.append({
                "role": EventParticipantRole.CAREGIVER,
                "id": evidence.observer_id,
                "name": evidence.observer_name,
                "resolved": True,
            })
        if evidence.observer_role and evidence.observer_role.value:
            participants.append({
                "role": evidence.observer_role.value,
                "name": evidence.observer_name,
                "resolved": False,
            })

        # Extract medication name if applicable
        title = self._extract_title(source_text, event_type, match)

        # Determine severity
        severity = self._determine_severity(source_text, event_type)
        severity_confidence = 0.7

        # Calculate confidence dimensions
        confidence = self._calculate_detection_confidence(evidence, event_type, detection_confidence)

        # Build state impacts
        state_impacts = self._infer_state_impacts(event_type, source_text)

        candidate = EventDetectionResult(
            event_type=event_type,
            original_expression=source_text,
            title=title,
            description=self._build_description(source_text, match),
            person_id=person_id,
            subject_role=EventSubjectRole.CARE_RECIPIENT,
            participants=participants,
            primary_caregiver_id=evidence.uploaded_by_caregiver_id,
            evidence_ids=[evidence.id],
            primary_evidence_id=evidence.id,
            source_ids=[evidence.source_id] if evidence.source_id else [],
            extraction_method=evidence.extraction_method.value if evidence.extraction_method else None,
            extraction_version=evidence.extraction_version,
            extraction_confidence=evidence.extraction_confidence,
            confidence=confidence,
            event_time=event_time,
            event_time_granularity=event_time_granularity,
            event_time_text=evidence.event_time_text,
            event_time_anchor=evidence.event_time_anchor.value if evidence.event_time_anchor else None,
            event_time_confidence=evidence.confidence_temporal,
            knowledge_time=knowledge_time,
            documentation_time=documentation_time,
            authoring_time=authoring_time,
            severity=severity,
            severity_confidence=severity_confidence,
            mention_type=mention_type,
            quality_gate_passed=self._check_quality_gate(evidence),
            created_by_caregiver_id=evidence.uploaded_by_caregiver_id,
            processing_version=f"detection_v{evidence.processing_version or '1.0'}",
        )

        # Validation
        self._validate_schema(candidate)
        self._validate_identity(candidate)
        self._validate_evidence_link(candidate)
        self._validate_temporal(candidate)
        self._validate_ontology(candidate)

        return candidate

    def _build_candidate_from_evidence_type(
        self,
        evidence: Evidence,
        event_type: CareEventType,
    ) -> Optional[EventDetectionResult]:
        """Build a detection candidate by mapping evidence type directly to event type."""
        person_id = evidence.subject_id
        if not person_id:
            return None

        # Extract temporal info
        event_time = evidence.event_time
        knowledge_time = evidence.observation_time or evidence.processing_timestamp
        documentation_time = evidence.document_time or evidence.authoring_time
        authoring_time = evidence.authoring_time

        # Build temporal info
        event_time_granularity = EventTimeGranularity.UNKNOWN
        if evidence.event_time_granularity:
            event_time_granularity = self._map_temporal_granularity(evidence.event_time_granularity)

        # Determine mention type
        mention_type = EventMentionType.CURRENT_OCCURRENCE
        if evidence.negation_status == NegationStatus.NEGATED:
            mention_type = EventMentionType.NEGATIVE_ASSERTION

        # Build participants
        participants = []
        if evidence.observer_id:
            participants.append({
                "role": EventParticipantRole.CAREGIVER,
                "id": evidence.observer_id,
                "name": evidence.observer_name,
                "resolved": True,
            })

        # Determine severity
        severity = self._determine_severity(evidence.source_text or "", event_type)
        severity_confidence = evidence.confidence_overall or 0.5

        # Calculate confidence dimensions
        confidence = self._calculate_detection_confidence(evidence, event_type, 0.8)

        # Build state impacts
        state_impacts = self._infer_state_impacts(event_type, evidence.source_text or "")

        candidate = EventDetectionResult(
            event_type=event_type,
            original_expression=evidence.source_text or "",
            title=self._extract_title(evidence.source_text or "", event_type, None),
            description=evidence.normalized_text,
            person_id=person_id,
            subject_role=EventSubjectRole.CARE_RECIPIENT,
            participants=participants,
            primary_caregiver_id=evidence.uploaded_by_caregiver_id,
            evidence_ids=[evidence.id],
            primary_evidence_id=evidence.id,
            source_ids=[evidence.source_id] if evidence.source_id else [],
            extraction_method=evidence.extraction_method.value if evidence.extraction_method else None,
            extraction_version=evidence.extraction_version,
            extraction_confidence=evidence.extraction_confidence,
            confidence=confidence,
            event_time=event_time,
            event_time_granularity=event_time_granularity,
            event_time_text=evidence.event_time_text,
            event_time_anchor=evidence.event_time_anchor.value if evidence.event_time_anchor else None,
            event_time_confidence=evidence.confidence_temporal,
            knowledge_time=knowledge_time,
            documentation_time=documentation_time,
            authoring_time=authoring_time,
            severity=severity,
            severity_confidence=severity_confidence,
            mention_type=mention_type,
            quality_gate_passed=self._check_quality_gate(evidence),
            created_by_caregiver_id=evidence.uploaded_by_caregiver_id,
            processing_version=f"detection_v{evidence.processing_version or '1.0'}",
        )

        # Validation
        self._validate_schema(candidate)
        self._validate_identity(candidate)
        self._validate_evidence_link(candidate)
        self._validate_temporal(candidate)
        self._validate_ontology(candidate)

        return candidate

    def _build_cross_evidence_candidate(
        self,
        primary_evidence: Evidence,
        secondary_evidence: Evidence,
        event_type: CareEventType,
        confidence: float,
        description: str,
    ) -> Optional[EventDetectionResult]:
        """Build a candidate that links multiple evidence items."""
        person_id = primary_evidence.subject_id
        if not person_id or person_id != secondary_evidence.subject_id:
            return None

        candidate = EventDetectionResult(
            event_type=event_type,
            original_expression=f"{primary_evidence.source_text} | {secondary_evidence.source_text}",
            title=self._extract_title(primary_evidence.source_text, event_type, None),
            description=description,
            person_id=person_id,
            subject_role=EventSubjectRole.CARE_RECIPIENT,
            evidence_ids=[primary_evidence.id, secondary_evidence.id],
            primary_evidence_id=primary_evidence.id,
            source_ids=list(set(
                eid for e in [primary_evidence.source_id, secondary_evidence.source_id] if eid
            )),
            confidence=self._calculate_detection_confidence(primary_evidence, event_type, confidence),
            event_time=primary_evidence.event_time,
            event_time_granularity=self._map_temporal_granularity(
                primary_evidence.event_time_granularity
            ) if primary_evidence.event_time_granularity else EventTimeGranularity.UNKNOWN,
            event_time_text=primary_evidence.event_time_text,
            mention_type=EventMentionType.CURRENT_OCCURRENCE,
            quality_gate_passed=self._check_quality_gate(primary_evidence),
            created_by_caregiver_id=primary_evidence.uploaded_by_caregiver_id,
            processing_version="cross_evidence_v1.0",
        )

        self._validate_schema(candidate)
        self._validate_identity(candidate)
        self._validate_evidence_link(candidate)
        self._validate_temporal(candidate)
        self._validate_ontology(candidate)

        return candidate

    def _extract_title(
        self,
        source_text: str,
        event_type: CareEventType,
        match: Optional[re.Match],
    ) -> str:
        """Extract a human-readable title for the event."""
        if event_type in (
            CareEventType.MEDICATION_TAKEN_EVENT,
            CareEventType.MEDICATION_START_EVENT,
            CareEventType.MEDICATION_STOP_EVENT,
            CareEventType.MEDICATION_MISSED_EVENT,
            CareEventType.MEDICATION_REFUSED_EVENT,
            CareEventType.MEDICATION_INSTRUCTION_EVENT,
        ):
            med_name = self._extract_medication_name(source_text)
            action = event_type.value.replace("_event", "").replace("_", " ")
            if med_name:
                return f"{action.title()}: {med_name}"
            return action.title()

        elif event_type == CareEventType.BLOOD_PRESSURE_MEASUREMENT:
            return "Blood Pressure Measurement"
        elif event_type == CareEventType.WEIGHT_MEASUREMENT:
            return "Weight Measurement"
        elif event_type == CareEventType.TEMPERATURE_MEASUREMENT:
            return "Temperature Measurement"
        elif event_type == CareEventType.GLUCOSE_MEASUREMENT:
            return "Glucose Measurement"
        elif event_type == CareEventType.FALL_EVENT:
            return "Fall Event"
        elif event_type == CareEventType.NEAR_FALL_EVENT:
            return "Near Fall"
        elif event_type == CareEventType.HOSPITALIZATION_EVENT:
            return "Hospitalization"
        elif event_type == CareEventType.APPOINTMENT_EVENT:
            return "Appointment"
        elif event_type == CareEventType.SYMPTOM_EVENT:
            return "Symptom Reported"
        elif event_type == CareEventType.OBSERVATION_EVENT:
            return "Clinical Observation"
        elif event_type == CareEventType.COMMUNICATION_EVENT:
            return "Communication Event"
        else:
            return event_type.value.replace("_", " ").title()

    def _extract_medication_name(self, text: str) -> Optional[str]:
        """Extract medication name from text using common patterns."""
        med_pattern = re.compile(
            r"\b(?:took|taken|gave|given|started|stopped|took|on|with)\s+"
            r"(?:(?:her|him|the|my|their)?\s*)?([\w\-]+(?:\s+[\w\-]+)*)"
            r"(?:\s+(?:tablet|pill|dose|mg|mcg|ml|daily|once|twice|times))?",
            re.IGNORECASE,
        )
        match = med_pattern.search(text)
        if match:
            name = match.group(1).strip()
            # Filter out common non-medication words
            stop_words = {"my", "the", "her", "him", "today", "this", "morning", "evening", "night", "dose", "mg"}
            if name.lower() not in stop_words and len(name) > 1:
                return name

        return None

    def _determine_severity(self, text: str, event_type: CareEventType) -> CareEventSeverity:
        """Determine severity from context."""
        text_lower = text.lower()

        if event_type in (CareEventType.FALL_EVENT, CareEventType.HOSPITALIZATION_EVENT, CareEventType.EMERGENCY_EVENT):
            if any(w in text_lower for w in ["injury", "broken", "fracture", "hospitalized", "er", "emergency"]):
                return CareEventSeverity.SEVERE
            if any(w in text_lower for w in ["near", "almost", "stumble"]):
                return CareEventSeverity.MODERATE
            return CareEventSeverity.MILD

        if event_type in (
            CareEventType.MEDICATION_MISSED_EVENT,
            CareEventType.MEDICATION_REFUSED_EVENT,
            CareEventType.MEDICATION_STOP_EVENT,
        ):
            if any(w in text_lower for w in ["important", "critical", "life", "key"]):
                return CareEventSeverity.MODERATE
            return CareEventSeverity.MILD

        if "severe" in text_lower or "serious" in text_lower or "critical" in text_lower:
            return CareEventSeverity.SEVERE
        if "mild" in text_lower:
            return CareEventSeverity.MILD

        return CareEventSeverity.UNKNOWN

    def _calculate_detection_confidence(
        self,
        evidence: Evidence,
        event_type: CareEventType,
        base_detection_confidence: float,
    ) -> EventDetectionConfidenceDimensions:
        """Calculate multi-dimensional confidence per Requirement 35."""
        # Event detection confidence
        event_detection = base_detection_confidence

        # Event type confidence (based on evidence type alignment)
        evidence_type_to_event_map: Dict[EvidenceType, CareEventType] = {
            EvidenceType.BLOOD_PRESSURE: CareEventType.BLOOD_PRESSURE_MEASUREMENT,
            EvidenceType.WEIGHT: CareEventType.WEIGHT_MEASUREMENT,
            EvidenceType.TEMPERATURE: CareEventType.TEMPERATURE_MEASUREMENT,
            EvidenceType.GLUCOSE: CareEventType.GLUCOSE_MEASUREMENT,
            EvidenceType.MEDICATION_TAKEN: CareEventType.MEDICATION_TAKEN_EVENT,
            EvidenceType.MEDICATION_MISSED: CareEventType.MEDICATION_MISSED_EVENT,
            EvidenceType.MEDICATION_REFUSED: CareEventType.MEDICATION_REFUSED_EVENT,
            EvidenceType.OBSERVATION: CareEventType.FALL_EVENT,
            EvidenceType.SYMPTOM_REPORT: CareEventType.SYMPTOM_EVENT,
            EvidenceType.APPOINTMENT_EVENT: CareEventType.APPOINTMENT_EVENT,
            EvidenceType.CAREGIVER_COMMUNICATION: CareEventType.COMMUNICATION_EVENT,
        }
        expected_event_type = evidence_type_to_event_map.get(evidence.type)
        event_type_conf = 1.0 if expected_event_type == event_type else 0.7

        # Subject confidence
        subject_conf = evidence.confidence_entity_resolution or (1.0 if evidence.subject_id else 0.5)

        # Temporal confidence
        temporal_conf = evidence.confidence_temporal or 0.5
        if evidence.event_time is not None:
            temporal_conf = max(temporal_conf, 0.8)

        # Identity confidence (based on evidence reliability)
        identity_conf = evidence.confidence_source_support or 0.7
        if evidence.hedge_type:
            identity_conf *= 0.7
        if evidence.uncertainty_level == UncertaintyLevel.EXPLICITLY_UNCERTAIN:
            identity_conf *= 0.6

        # Severity confidence
        severity_conf = 0.7
        if evidence.confidence_overall:
            severity_conf = evidence.confidence_overall

        # Status confidence
        status_conf = evidence.confidence_interpretation or 0.7

        # Outcome confidence (unknown for new events)
        outcome_conf = None

        # Overall confidence
        dims = [event_detection, event_type_conf, subject_conf, temporal_conf, identity_conf, severity_conf, status_conf]
        valid_dims = [d for d in dims if d is not None]
        overall = sum(valid_dims) / len(valid_dims) if valid_dims else 0.5

        return EventDetectionConfidenceDimensions(
            event_detection=round(event_detection, 2),
            event_type=round(event_type_conf, 2),
            subject=round(subject_conf, 2),
            temporal=round(temporal_conf, 2),
            identity=round(identity_conf, 2),
            severity=round(severity_conf, 2),
            status=round(status_conf, 2),
            outcome=outcome_conf,
            overall=round(overall, 2),
        )

    def _infer_state_impacts(self, event_type: CareEventType, text: str) -> Optional[List[Dict[str, Any]]]:
        """Infer potential state impacts based on event type.

        These are tentative and validated by the State Impact Service.
        """
        impacts = []

        if event_type == CareEventType.FALL_EVENT:
            impacts.append({
                "state_type": "mobility",
                "impact_type": "changes",
                "confidence": "possible",
                "reasoning_rule": "fall_implies_mobility_change",
                "evidence_ids": [],
            })
            impacts.append({
                "state_type": "injury_status",
                "impact_type": "creates",
                "confidence": "possible",
                "reasoning_rule": "fall_implies_injury_risk",
            })

        elif event_type == CareEventType.HOSPITALIZATION_EVENT:
            impacts.append({
                "state_type": "care_intensity",
                "impact_type": "increases",
                "confidence": "supported",
                "reasoning_rule": "hospitalization_implies_care_intensity",
            })

        elif event_type == CareEventType.MEDICATION_START_EVENT:
            impacts.append({
                "state_type": "medication_regimen",
                "impact_type": "changes",
                "confidence": "supported",
                "reasoning_rule": "med_start_implies_regimen_change",
            })

        elif event_type == CareEventType.MEDICATION_STOP_EVENT:
            impacts.append({
                "state_type": "medication_regimen",
                "impact_type": "changes",
                "confidence": "supported",
                "reasoning_rule": "med_stop_implies_regimen_change",
            })

        elif event_type == CareEventType.MEDICATION_MISSED_EVENT:
            impacts.append({
                "state_type": "medication_adherence",
                "impact_type": "changes",
                "confidence": "supported",
                "reasoning_rule": "missed_dose_implies_adherence_change",
            })

        elif event_type == CareEventType.SYMPTOM_EVENT:
            impacts.append({
                "state_type": "symptom_status",
                "impact_type": "creates",
                "confidence": "possible",
                "reasoning_rule": "symptom_report_implies_symptom_state",
            })

        elif event_type == CareEventType.COGNITIVE_OBSERVATION:
            impacts.append({
                "state_type": "cognitive_status",
                "impact_type": "changes",
                "confidence": "possible",
                "reasoning_rule": "cognitive_observation_implies_cognitive_state",
            })

        elif event_type == CareEventType.MEDICATION_TAKEN_EVENT:
            impacts.append({
                "state_type": "medication_adherence",
                "impact_type": "supports",
                "confidence": "supported",
                "reasoning_rule": "med_taken_supports_adherence",
            })

        if impacts:
            return impacts
        return None

    def _check_quality_gate(self, evidence: Evidence) -> bool:
        """Check if evidence passes quality gate for event detection."""
        if evidence.status != EvidenceStatus.ACTIVE:
            return False
        if evidence.quality_gate_passed is False:
            # If quality gate was explicitly set, respect it; otherwise default to True
            if hasattr(evidence, 'quality_gate_passed') and evidence.quality_gate_passed is not None:
                return evidence.quality_gate_passed
        return True

    def _validate_schema(self, candidate: EventDetectionResult) -> None:
        """Validate schema constraints. Requirement 104."""
        errors: List[str] = []

        if not candidate.person_id:
            errors.append("person_id is required")
            candidate.required_fields_present = False
            candidate.missing_required_fields.append("person_id")

        if not candidate.event_type:
            errors.append("event_type is required")
            candidate.required_fields_present = False
            candidate.missing_required_fields.append("event_type")

        if not candidate.original_expression:
            errors.append("original_expression is required")
            candidate.required_fields_present = False
            candidate.missing_required_fields.append("original_expression")

        if not candidate.title:
            errors.append("title is required")
            candidate.required_fields_present = False
            candidate.missing_required_fields.append("title")

        if not candidate.created_by_caregiver_id:
            errors.append("created_by_caregiver_id is required")
            candidate.required_fields_present = False
            candidate.missing_required_fields.append("created_by_caregiver_id")

        if not candidate.evidence_ids:
            errors.append("evidence_ids is required")
            candidate.required_fields_present = False
            candidate.missing_required_fields.append("evidence_ids")

        candidate.validation_errors.extend(errors)

    def _validate_identity(self, candidate: EventDetectionResult) -> None:
        """Validate identity resolution. Requirement 108."""
        errors: List[str] = []

        # Check that person_id matches evidence subject
        if candidate.evidence_ids:
            # This would be validated against actual evidence in the database
            pass

        # Check that entity resolution status is appropriate
        if candidate.entity_resolution_status == "unresolved" and not candidate.subject_id:
            candidate.entity_resolution_confidence = None

        candidate.validation_errors.extend(errors)

    def _validate_evidence_link(self, candidate: EventDetectionResult) -> None:
        """Validate evidence links are active and valid. Requirement 110."""
        errors: List[str] = []
        # Evidence validation happens at the service level
        # (checked against db in _validate_candidate)
        candidate.validation_errors.extend(errors)

    def _validate_temporal(self, candidate: EventDetectionResult) -> None:
        """Validate temporal constraints. Requirement 111."""
        errors: List[str] = []

        # If we have event_time, it should not be in the future relative to knowledge_time
        if (
            candidate.event_time
            and candidate.knowledge_time
            and candidate.event_time > candidate.knowledge_time + timedelta(hours=24)
        ):
            errors.append(
                f"event_time ({candidate.event_time}) is too far in the future relative to knowledge_time ({candidate.knowledge_time})"
            )

        candidate.validation_errors.extend(errors)

    def _validate_ontology(self, candidate: EventDetectionResult) -> None:
        """Validate event type/subtype/severity combinations. Requirement 112."""
        errors: List[str] = []

        # Validate subtype compatibility
        if candidate.event_type and candidate.event_subtype:
            if candidate.event_type not in self._get_subtype_map().get(candidate.event_subtype, set()):
                errors.append(
                    f"event_subtype {candidate.event_subtype} is not valid for event_type {candidate.event_type}"
                )

        candidate.validation_errors.extend(errors)

    def _get_subtype_map(self) -> Dict[CareEventSubtype, set]:
        """Return mapping of subtypes to valid event types."""
        return {
            CareEventSubtype.FALL_WITNESSED: {CareEventType.FALL_EVENT},
            CareEventSubtype.FALL_UNWITNESSED: {CareEventType.FALL_EVENT},
            CareEventSubtype.FALL_REPORTED: {CareEventType.FALL_EVENT},
            CareEventSubtype.MEDICATION_STARTED: {CareEventType.MEDICATION_START_EVENT},
            CareEventSubtype.MEDICATION_STOPPED: {CareEventType.MEDICATION_STOP_EVENT},
            CareEventSubtype.MEDICATION_CHANGE: {CareEventType.MEDICATION_CHANGE_EVENT},
            CareEventSubtype.APPOINTMENT_SCHEDULED: {CareEventType.APPOINTMENT_EVENT},
            CareEventSubtype.APPOINTMENT_COMPLETED: {CareEventType.APPOINTMENT_EVENT},
            CareEventSubtype.APPOINTMENT_CANCELLED: {CareEventType.APPOINTMENT_EVENT},
            CareEventSubtype.APPOINTMENT_MISSED: {CareEventType.APPOINTMENT_EVENT},
        }

    def _validate_candidate(
        self,
        db: Session,
        candidate: EventDetectionResult,
        evidence_items: List[Evidence],
    ) -> None:
        """Full validation of candidate against database state."""
        errors: List[str] = list(candidate.validation_errors)

        # Validate evidence IDs exist and are active
        evidence_map = {ev.id: ev for ev in evidence_items}
        for eid in candidate.evidence_ids:
            evidence = evidence_map.get(eid)
            if not evidence:
                errors.append(f"Evidence {eid} not found in available evidence")
            elif evidence.status != EvidenceStatus.ACTIVE:
                errors.append(f"Evidence {eid} is not active (status: {evidence.status})")

        # Validate person_id exists
        from app.models.care import Person
        person = db.query(Person).filter(Person.id == candidate.person_id).first()
        if not person:
            errors.append(f"Person {candidate.person_id} not found")

        # Validate person matches evidence subject
        for eid in candidate.evidence_ids:
            evidence = evidence_map.get(eid)
            if evidence and evidence.subject_id != candidate.person_id:
                errors.append(
                    f"Person mismatch: candidate person_id={candidate.person_id} "
                    f"vs evidence subject_id={evidence.subject_id}"
                )

        if errors:
            candidate.validation_errors.extend(errors)
            candidate.validation_status = "invalid"
            candidate.quality_gate_passed = False
        else:
            candidate.validation_status = "valid"
            candidate.quality_gate_passed = True

    def _build_description(self, source_text: str, match: re.Match) -> Optional[str]:
        """Build description from match context."""
        return source_text.strip() if source_text else None

    def _events_temporally_related(
        self,
        evidence_a: Evidence,
        evidence_b: Evidence,
        max_gap_hours: float = 24,
    ) -> bool:
        """Check if two evidence items are temporally related."""
        if not evidence_a.event_time or not evidence_b.event_time:
            return False

        time_diff = abs((evidence_a.event_time - evidence_b.event_time).total_seconds())
        return time_diff <= (max_gap_hours * 3600)

    def _map_temporal_granularity(self, granularity: TemporalGranularity) -> Optional[EventTimeGranularity]:
        """Map evidence temporal granularity to event temporal granularity."""
        mapping = {
            TemporalGranularity.EXACT_TIMESTAMP: EventTimeGranularity.EXACT_TIMESTAMP,
            TemporalGranularity.DATE: EventTimeGranularity.DATE,
            TemporalGranularity.MONTH_YEAR: EventTimeGranularity.MONTH_YEAR,
            TemporalGranularity.RELATIVE: EventTimeGranularity.RELATIVE,
            TemporalGranularity.APPROXIMATE: EventTimeGranularity.APPROXIMATE,
            TemporalGranularity.TIME_RANGE: EventTimeGranularity.TIME_RANGE,
        }
        return mapping.get(granularity, EventTimeGranularity.UNKNOWN)

    def detect_event_mention(
        self,
        source_text: str,
        evidence: Evidence,
        mention_type: EventMentionType,
    ) -> Optional[EventDetectionResult]:
        """Detect a single event mention from text.

        Used for individual evidence processing.
        """
        # Check if text contains extractable information
        if not source_text or not source_text.strip():
            return None

        # Determine event type from patterns
        event_type = None
        match = None

        for etype, patterns in self._compiled_patterns.items():
            for pattern, subtype, severity, status, conf in patterns:
                m = pattern.search(source_text)
                if m:
                    event_type = etype
                    match = m
                    break
            if event_type:
                break

        # Fall back to evidence type mapping
        if not event_type:
            evidence_to_event_map: Dict[EvidenceType, CareEventType] = {
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
            event_type = evidence_to_event_map.get(evidence.type)

        if not event_type:
            return None

        # Build candidate using the existing method
        if match:
            return self._build_candidate(evidence, event_type, match, source_text, 0.8)
        else:
            return self._build_candidate_from_evidence_type(evidence, event_type)

    def batch_detect(
        self,
        db: Session,
        evidence_ids: List[str],
        person_id: Optional[str] = None,
    ) -> List[EventDetectionResult]:
        """Batch detect events from multiple evidence items."""
        return self.detect_events_from_evidence(db, evidence_ids=evidence_ids, person_id=person_id)
