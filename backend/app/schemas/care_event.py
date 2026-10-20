"""Pydantic schemas for Care Event Engine API."""

from pydantic import BaseModel, Field, ConfigDict
from datetime import datetime
from typing import Optional, List, Dict, Any, Literal
from app.core.event_enums import (
    CareEventType,
    CareEventSubtype,
    CareEventStatus,
    CareEventSeverity,
    CareEventRelationshipType,
    CareEventGroupingType,
    EventStateImpactType,
    EventStateImpactConfidence,
    EventDetectionConfidenceDimension,
    EventMentionDeduplicationStatus,
    EventCorrectionType,
    EventInvalidationReason,
    EventSupersessionReason,
    EventLifecycleStage,
    EventTimeGranularity,
    EventDurationType,
    EventSubjectRole,
    EventParticipantRole,
    RecurrenceDetectionMethod,
    EventMentionType,
)


class ParticipantInfo(BaseModel):
    """Participant in a care event."""
    role: EventParticipantRole
    id: Optional[str] = None
    name: Optional[str] = None
    resolved: bool = False


class StateImpactDetail(BaseModel):
    """Detail of an event's impact on a care state."""
    state_type: str
    impact_type: EventStateImpactType
    confidence: EventStateImpactConfidence
    details: Optional[Dict[str, Any]] = None
    evidence_ids: List[str] = []
    reasoning_rule: Optional[str] = None
    temporal_relationship: Optional[str] = None
    time_lag_days: Optional[float] = None


class UnresolvedQuestion(BaseModel):
    """Unresolved question for Next Best Understanding."""
    question: str
    priority: Literal["critical", "high", "moderate", "low"] = "moderate"
    evidence_ids: List[str] = []
    related_event_ids: List[str] = []


class ChangeSignal(BaseModel):
    """Change detection signal from an event."""
    signal_type: str
    strength: float = Field(ge=0.0, le=1.0)
    evidence_ids: List[str] = []


class WatchSignal(BaseModel):
    """Watch signal from an event."""
    signal_type: str
    priority: Literal["critical", "high", "moderate", "low"] = "moderate"
    evidence_ids: List[str] = []


class ConfidenceDimensions(BaseModel):
    """Multi-dimensional confidence scores for event detection."""
    event_detection: Optional[float] = Field(None, ge=0.0, le=1.0)
    event_type: Optional[float] = Field(None, ge=0.0, le=1.0)
    subject: Optional[float] = Field(None, ge=0.0, le=1.0)
    temporal: Optional[float] = Field(None, ge=0.0, le=1.0)
    identity: Optional[float] = Field(None, ge=0.0, le=1.0)
    severity: Optional[float] = Field(None, ge=0.0, le=1.0)
    status: Optional[float] = Field(None, ge=0.0, le=1.0)
    outcome: Optional[float] = Field(None, ge=0.0, le=1.0)
    overall: Optional[float] = Field(None, ge=0.0, le=1.0)


class CareEventBase(BaseModel):
    """Base care event schema with all core fields."""
    # Subject
    person_id: str
    subject_role: EventSubjectRole = EventSubjectRole.CARE_RECIPIENT
    subject_id: Optional[str] = None
    subject_label: Optional[str] = None
    entity_resolution_status: str = "resolved"
    entity_resolution_confidence: Optional[float] = Field(None, ge=0.0, le=1.0)

    # Classification
    event_type: CareEventType
    event_subtype: Optional[CareEventSubtype] = None
    event_category: Optional[str] = None

    # Temporal
    event_time: Optional[datetime] = None
    event_time_granularity: Optional[EventTimeGranularity] = None
    event_time_text: Optional[str] = None
    event_time_anchor: Optional[str] = None
    event_time_lower_bound: Optional[datetime] = None
    event_time_upper_bound: Optional[datetime] = None
    event_time_confidence: Optional[float] = Field(None, ge=0.0, le=1.0)

    # Duration
    event_duration: Optional[str] = None
    event_duration_type: Optional[EventDurationType] = None
    event_duration_value: Optional[float] = None
    event_duration_unit: Optional[str] = None
    event_duration_confidence: Optional[float] = Field(None, ge=0.0, le=1.0)
    event_duration_is_explicit: bool = False

    # Status
    status: CareEventStatus = CareEventStatus.UNCONFIRMED
    status_reason: Optional[str] = None
    status_confidence: Optional[float] = Field(None, ge=0.0, le=1.0)

    # Severity
    severity: CareEventSeverity = CareEventSeverity.UNKNOWN
    severity_confidence: Optional[float] = Field(None, ge=0.0, le=1.0)
    severity_details: Optional[str] = None

    # Content
    title: str
    description: Optional[str] = None
    original_expression: str  # REQUIRED - never replace raw expression
    normalized_text: Optional[str] = None

    # Participants
    participants: Optional[List[ParticipantInfo]] = None
    primary_caregiver_id: Optional[str] = None
    primary_provider_id: Optional[str] = None
    facility_id: Optional[str] = None
    location: Optional[str] = None
    location_provenance: Optional[str] = None

    # Evidence & Provenance
    evidence_ids: Optional[List[str]] = None
    primary_evidence_id: Optional[str] = None
    source_ids: Optional[List[str]] = None
    extraction_method: Optional[str] = None
    extraction_version: Optional[str] = None
    extraction_confidence: Optional[float] = Field(None, ge=0.0, le=1.0)

    # Confidence
    confidence: Optional[ConfidenceDimensions] = None

    # Knowledge Time vs Event Time
    knowledge_time: Optional[datetime] = None
    documentation_time: Optional[datetime] = None
    authoring_time: Optional[datetime] = None

    # Lifecycle
    lifecycle_stage: EventLifecycleStage = EventLifecycleStage.DETECTED
    superseded_by_event_id: Optional[str] = None

    # Recurrence
    recurrence_group_id: Optional[str] = None
    recurrence_sequence: Optional[int] = None
    recurrence_detection_method: Optional[RecurrenceDetectionMethod] = None
    recurrence_confidence: Optional[float] = Field(None, ge=0.0, le=1.0)
    is_recurrence_representative: bool = False

    # Grouping
    group_id: Optional[str] = None
    group_type: Optional[CareEventGroupingType] = None
    group_role: Optional[str] = None

    # Outcomes
    outcome: Optional[str] = None
    outcome_type: Optional[str] = None
    outcome_confidence: Optional[float] = Field(None, ge=0.0, le=1.0)
    outcome_evidence_ids: Optional[List[str]] = None
    outcome_time: Optional[datetime] = None
    outcome_time_granularity: Optional[EventTimeGranularity] = None

    # State Impact
    state_impacts: Optional[List[StateImpactDetail]] = None
    state_impact_confidence: Optional[float] = Field(None, ge=0.0, le=1.0)

    # Situation
    situation_id: Optional[str] = None
    situation_role: Optional[str] = None

    # Change/Watch/Next Best Understanding
    change_signals: Optional[List[ChangeSignal]] = None
    watch_signals: Optional[List[WatchSignal]] = None
    unresolved_questions: Optional[List[UnresolvedQuestion]] = None

    # Completeness
    required_fields_present: bool = True
    missing_required_fields: Optional[List[str]] = None

    # Validation
    validation_status: str = "pending"
    validation_errors: Optional[List[str]] = None

    # Mention type
    mention_type: EventMentionType = EventMentionType.CURRENT_OCCURRENCE
    mention_source_event_id: Optional[str] = None

    # Processing
    processing_version: Optional[str] = None

    # Quality gate
    quality_gate_passed: bool = False
    quality_gate_details: Optional[Dict[str, Any]] = None

    created_by_caregiver_id: str


class CareEventCreate(CareEventBase):
    """Schema for creating new care event."""
    pass


class CareEventUpdate(BaseModel):
    """Schema for updating care event (limited fields)."""
    status: Optional[CareEventStatus] = None
    status_reason: Optional[str] = None
    severity: Optional[CareEventSeverity] = None
    severity_confidence: Optional[float] = Field(None, ge=0.0, le=1.0)
    severity_details: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    normalized_text: Optional[str] = None
    location: Optional[str] = None
    location_provenance: Optional[str] = None
    outcome: Optional[str] = None
    outcome_confidence: Optional[float] = Field(None, ge=0.0, le=1.0)
    outcome_evidence_ids: Optional[List[str]] = None
    state_impacts: Optional[List[StateImpactDetail]] = None
    situation_id: Optional[str] = None
    lifecycle_stage: Optional[EventLifecycleStage] = None
    superseded_by_event_id: Optional[str] = None
    confidence: Optional[ConfidenceDimensions] = None
    validation_status: Optional[str] = None
    quality_gate_passed: Optional[bool] = None
    quality_gate_details: Optional[Dict[str, Any]] = None


class CareEventRelationshipCreate(BaseModel):
    """Create care event relationship."""
    source_event_id: str
    target_event_id: str
    relationship_type: CareEventRelationshipType
    is_temporal: bool = True
    is_causal: bool = False
    causal_evidence_ids: Optional[List[str]] = None
    temporal_relationship: Optional[str] = None
    time_gap_seconds: Optional[float] = None
    strength: Optional[float] = Field(None, ge=0.0, le=1.0)
    confidence: Optional[float] = Field(None, ge=0.0, le=1.0)
    evidence_ids: Optional[List[str]] = None
    detection_method: Optional[str] = None
    detection_confidence: Optional[float] = Field(None, ge=0.0, le=1.0)


class CareEventRelationshipResponse(BaseModel):
    """Care event relationship with details."""
    id: str
    source_event_id: str
    target_event_id: str
    relationship_type: CareEventRelationshipType
    is_temporal: bool
    is_causal: bool
    causal_evidence_ids: List[str] = []
    temporal_relationship: Optional[str] = None
    time_gap_seconds: Optional[float] = None
    strength: Optional[float] = None
    confidence: Optional[float] = None
    evidence_ids: List[str] = []
    detection_method: Optional[str] = None
    detection_confidence: Optional[float] = None
    is_active: bool
    causal_resolution_status: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None
    created_by: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class CareEventRecurrenceGroupCreate(BaseModel):
    """Create recurrence group."""
    person_id: str
    event_type: CareEventType
    event_subtype: Optional[CareEventSubtype] = None
    member_event_ids: List[str]
    pattern_type: Optional[str] = None
    detection_method: Optional[RecurrenceDetectionMethod] = None
    detection_evidence_ids: Optional[List[str]] = None
    representative_event_id: Optional[str] = None


class CareEventRecurrenceGroupResponse(BaseModel):
    """Recurrence group response."""
    id: str
    person_id: str
    event_type: CareEventType
    event_subtype: Optional[CareEventSubtype] = None
    first_occurrence_time: Optional[datetime] = None
    last_occurrence_time: Optional[datetime] = None
    occurrence_count: int
    pattern_type: Optional[str] = None
    average_interval_days: Optional[float] = None
    interval_std_dev_days: Optional[float] = None
    frequency_per_month: Optional[float] = None
    trend: Optional[str] = None
    pattern_confidence: Optional[float] = None
    grouping_confidence: Optional[float] = None
    detection_method: Optional[RecurrenceDetectionMethod] = None
    detection_evidence_ids: List[str] = []
    is_active: bool
    status: str
    representative_event_id: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class CareEventGroupCreate(BaseModel):
    """Create event group."""
    person_id: str
    group_type: CareEventGroupingType
    title: str
    description: Optional[str] = None
    member_event_ids: List[str]
    grouping_reason: Optional[str] = None


class CareEventGroupResponse(BaseModel):
    """Event group response."""
    id: str
    person_id: str
    group_type: CareEventGroupingType
    title: str
    description: Optional[str] = None
    temporal_start: Optional[datetime] = None
    temporal_end: Optional[datetime] = None
    member_event_ids: List[str] = []
    member_count: int
    grouping_confidence: Optional[float] = None
    grouping_reason: Optional[str] = None
    is_active: bool
    status: str
    created_at: datetime
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class CareEventCorrectionCreate(BaseModel):
    """Create event correction."""
    care_event_id: str
    correction_type: EventCorrectionType
    field_name: str
    old_value: Optional[str] = None
    new_value: Optional[str] = None
    correction_reason: Optional[str] = None
    correction_evidence_ids: Optional[List[str]] = None
    is_source_correction: bool = False
    source_correction_details: Optional[str] = None


class CareEventCorrectionResponse(BaseModel):
    """Event correction with propagation info."""
    id: str
    care_event_id: str
    correction_type: EventCorrectionType
    field_name: str
    old_value: Optional[str]
    new_value: Optional[str]
    correction_reason: Optional[str]
    is_source_correction: bool
    affected_relationships: List[str] = []
    affected_recurrence_groups: List[str] = []
    affected_groups: List[str] = []
    affected_situations: List[str] = []
    affected_state_impacts: List[str] = []
    affected_change_signals: List[str] = []
    affected_watches: List[str] = []
    affected_summaries: List[str] = []
    applied: bool
    applied_at: Optional[datetime]
    application_details: Optional[str] = None
    created_at: datetime
    corrected_by: str

    model_config = ConfigDict(from_attributes=True)


class CareEventInvalidationCreate(BaseModel):
    """Create event invalidation."""
    care_event_id: str
    reason: EventInvalidationReason
    reason_details: Optional[str] = None


class CareEventInvalidationResponse(BaseModel):
    """Event invalidation with propagation tracking."""
    id: str
    care_event_id: str
    reason: EventInvalidationReason
    reason_details: Optional[str]
    propagation_status: str
    affected_relationships: List[str] = []
    affected_recurrence_groups: List[str] = []
    affected_groups: List[str] = []
    affected_situations: List[str] = []
    affected_state_impacts: List[str] = []
    affected_change_signals: List[str] = []
    affected_watches: List[str] = []
    affected_summaries: List[str] = []
    affected_next_actions: List[str] = []
    propagation_log: List[Dict[str, Any]] = []
    created_at: datetime
    completed_at: Optional[datetime]
    invalidated_by: str

    model_config = ConfigDict(from_attributes=True)


class CareEventSupersessionCreate(BaseModel):
    """Create event supersession."""
    superseded_event_id: str
    superseding_event_id: str
    reason: EventSupersessionReason
    reason_details: Optional[str] = None
    effective_from: datetime
    is_explicit: bool = True


class CareEventSupersessionResponse(BaseModel):
    """Event supersession."""
    id: str
    superseded_event_id: str
    superseding_event_id: str
    reason: EventSupersessionReason
    reason_details: Optional[str]
    effective_from: datetime
    is_explicit: bool
    established_by: Optional[str] = None
    established_by_system: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class CareEventMentionDeduplicationCreate(BaseModel):
    """Propose event mention deduplication."""
    mention_a_id: str
    mention_b_id: str
    similarity_score: Optional[float] = Field(None, ge=0.0, le=1.0)
    match_criteria: Optional[List[str]] = None
    match_details: Optional[Dict[str, Any]] = None


class CareEventMentionDeduplicationResponse(BaseModel):
    """Event mention deduplication record."""
    id: str
    mention_a_id: str
    mention_b_id: str
    similarity_score: Optional[float]
    match_criteria: List[str] = []
    match_details: Optional[Dict[str, Any]] = None
    status: EventMentionDeduplicationStatus
    confirmed_by: Optional[str] = None
    resolved_event_id: Optional[str] = None
    resolution_reason: Optional[str] = None
    created_at: datetime
    confirmed_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class CareEventStateImpactCreate(BaseModel):
    """Create event state impact."""
    care_event_id: str
    state_type: str
    state_subject_id: Optional[str] = None
    state_subject_role: EventSubjectRole = EventSubjectRole.CARE_RECIPIENT
    impact_type: EventStateImpactType
    impact_confidence: EventStateImpactConfidence = EventStateImpactConfidence.UNKNOWN
    impact_details: Optional[Dict[str, Any]] = None
    supporting_evidence_ids: Optional[List[str]] = None
    supporting_care_event_ids: Optional[List[str]] = None
    reasoning_rule: Optional[str] = None
    reasoning_confidence: Optional[float] = Field(None, ge=0.0, le=1.0)
    temporal_relationship: Optional[str] = None
    time_lag_days: Optional[float] = None


class CareEventStateImpactResponse(BaseModel):
    """Event state impact response."""
    id: str
    care_event_id: str
    state_type: str
    state_subject_id: Optional[str] = None
    state_subject_role: EventSubjectRole
    impact_type: EventStateImpactType
    impact_confidence: EventStateImpactConfidence
    impact_details: Optional[Dict[str, Any]] = None
    supporting_evidence_ids: List[str] = []
    supporting_care_event_ids: List[str] = []
    reasoning_rule: Optional[str] = None
    reasoning_confidence: Optional[float] = None
    temporal_relationship: Optional[str] = None
    time_lag_days: Optional[float] = None
    validated_by_state_engine: bool
    state_engine_validation_time: Optional[datetime] = None
    state_engine_validation_notes: Optional[str] = None
    is_revoked: bool
    revoked_at: Optional[datetime] = None
    superseded_by_impact_id: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class CareEventVersionResponse(BaseModel):
    """Care event version history."""
    id: str
    care_event_id: str
    version: int
    change_type: str
    change_reason: Optional[str]
    changed_by: Optional[str] = None
    changed_by_system: bool
    affected_downstream: List[str] = []
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class CareEventResponse(CareEventBase):
    """Full care event response with all fields."""
    id: str
    version: int
    recorded_at: datetime
    created_at: datetime
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class CareEventQueryParams(BaseModel):
    """Structured care event query parameters."""
    person_id: Optional[str] = None
    subject_id: Optional[str] = None
    subject_role: Optional[EventSubjectRole] = None
    event_type: Optional[CareEventType] = None
    event_subtype: Optional[CareEventSubtype] = None
    event_category: Optional[str] = None
    status: Optional[CareEventStatus] = None
    severity: Optional[CareEventSeverity] = None
    lifecycle_stage: Optional[EventLifecycleStage] = None
    recurrence_group_id: Optional[str] = None
    group_id: Optional[str] = None
    group_type: Optional[CareEventGroupingType] = None
    situation_id: Optional[str] = None
    mention_type: Optional[EventMentionType] = None
    primary_evidence_id: Optional[str] = None
    source_id: Optional[str] = None
    event_time_from: Optional[datetime] = None
    event_time_to: Optional[datetime] = None
    knowledge_time_from: Optional[datetime] = None
    knowledge_time_to: Optional[datetime] = None
    confidence_overall_min: Optional[float] = Field(None, ge=0.0, le=1.0)
    confidence_temporal_min: Optional[float] = Field(None, ge=0.0, le=1.0)
    confidence_type_min: Optional[float] = Field(None, ge=0.0, le=1.0)
    has_relationships: Optional[bool] = None
    relationship_type: Optional[CareEventRelationshipType] = None
    related_to_event_id: Optional[str] = None
    has_state_impacts: Optional[bool] = None
    state_impact_type: Optional[EventStateImpactType] = None
    quality_gate_passed: Optional[bool] = None
    limit: int = Field(100, ge=1, le=1000)
    offset: int = Field(0, ge=0)
    sort_by: str = "event_time"
    sort_order: Literal["asc", "desc"] = "desc"


class CareEventTraceResponse(BaseModel):
    """Full traceability chain for a care event."""
    event: CareEventResponse
    evidence_chain: List[Dict[str, Any]] = []
    source_documents: List[Dict[str, Any]] = []
    versions: List[CareEventVersionResponse] = []
    relationships: List[CareEventRelationshipResponse] = []
    corrections: List[CareEventCorrectionResponse] = []
    invalidations: List[CareEventInvalidationResponse] = []
    supersessions: List[CareEventSupersessionResponse] = []
    mention_deduplications: List[CareEventMentionDeduplicationResponse] = []
    recurrence_group: Optional[CareEventRecurrenceGroupResponse] = None
    group: Optional[CareEventGroupResponse] = None
    state_impacts: List[CareEventStateImpactResponse] = []
    downstream_situations: List[Dict[str, Any]] = []
    downstream_changes: List[Dict[str, Any]] = []
    downstream_watches: List[Dict[str, Any]] = []


class CareEventGraphResponse(BaseModel):
    """Care event graph for a person."""
    nodes: List[CareEventResponse]
    edges: List[CareEventRelationshipResponse]
    recurrence_groups: List[CareEventRecurrenceGroupResponse] = []
    groups: List[CareEventGroupResponse] = []


class HistoricalKnowledgeQuery(BaseModel):
    """Query for historical knowledge reconstruction (Requirement 132)."""
    person_id: str
    as_of: datetime
    event_types: Optional[List[CareEventType]] = None
    include_superseded: bool = True
    include_invalidated: bool = False


class HistoricalKnowledgeResponse(BaseModel):
    """What SolenOS believed at a point in time."""
    as_of: datetime
    person_id: str
    events: List[CareEventResponse]
    event_versions_used: Dict[str, int] = {}  # event_id -> version
    supersessions_effective: List[str] = []
    invalidations_effective: List[str] = []
    confidence_notes: str = ""