"""Canonical Care Event model - the foundational longitudinal domain object for SolenOS.
# care_events = relationship("CareEvent", foreign_keys="CareEvent.person_id", back_populates="person")
- First-class longitudinal domain object with stable identity
- Strict separation: Source -> Evidence -> Event -> Relationships -> State Impact -> Situation
- Full provenance chain to evidence
- Versioning with immutable history
- Typed relationships with temporal and causal distinction
- Recurrence grouping without identity loss
- Correction/invalidation/supersession with audit trail
- State impact traceability
"""

from sqlalchemy import Column, String, DateTime, Text, Float, Integer, ForeignKey, Boolean, Enum as SQLEnum, Index, UniqueConstraint, JSON
from sqlalchemy.orm import relationship, synonym
from sqlalchemy.sql import func
import uuid
from app.core.database import Base
from app.core.types import JSONList, JSONDict
from app.models.longitudinal_evidence import LongitudinalEvidenceEvent, LongitudinalEvidenceSnapshot
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


class CareEvent(Base):
    """Core Care Event entity - represents a care-relevant occurrence.

    Key design principles (Requirements 10-12, 30-36):
    - Stable identity: repeated ingestion of same source does not create new events
    - Event ≠ Evidence ≠ Claim ≠ State ≠ Situation ≠ Outcome
    - Every event traceable to evidence with full provenance chain
    - Multi-dimensional confidence (not single scalar)
    - Person boundary absolute (Requirement 32)
    - Versioning with immutable history (Requirements 65, 66)
    - Knowledge time vs event time distinction (Requirement 68)
    """
    __tablename__ = "care_events"

    # Identity (Requirement 11)
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    version = Column(Integer, nullable=False, default=1)

    # Subject - WHO this event is about (Requirements 31, 32)
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    subject_role = Column(SQLEnum(EventSubjectRole), nullable=False, default=EventSubjectRole.CARE_RECIPIENT)
    subject_id = Column(String, ForeignKey("persons.id"), nullable=True, index=True)  # When subject != person_id
    subject_label = Column(String, nullable=True)  # Free-text when not in system
    entity_resolution_status = Column(String, nullable=False, default="resolved")
    entity_resolution_confidence = Column(Float, nullable=True)

    # Event Classification (Requirements 14, 15, 16)
    event_type = Column(SQLEnum(CareEventType), nullable=False, index=True)
    event_subtype = Column(SQLEnum(CareEventSubtype), nullable=True)
    event_category = Column(String, nullable=True)  # Higher-level grouping for queries

    # Temporal Representation (Requirements 28, 29) - delegates to Temporal Intelligence
    event_time = Column(DateTime(timezone=True), nullable=True, index=True)  # When the event occurred
    occurred_at = synonym("event_time")  # Backward-compatible alias
    event_time_granularity = Column(SQLEnum(EventTimeGranularity), nullable=True)
    event_time_text = Column(String, nullable=True)  # Original temporal expression ("yesterday", "last week")
    event_time_anchor = Column(String, nullable=True)  # What this time anchors to
    event_time_lower_bound = Column(DateTime(timezone=True), nullable=True)
    event_time_upper_bound = Column(DateTime(timezone=True), nullable=True)
    event_time_confidence = Column(Float, nullable=True)  # Temporal confidence dimension

    # Duration (Requirement 27)
    event_duration = Column(String, nullable=True)  # ISO 8601 duration or descriptive
    event_duration_type = Column(SQLEnum(EventDurationType), nullable=True)
    event_duration_value = Column(Float, nullable=True)  # Numeric duration
    event_duration_unit = Column(String, nullable=True)  # seconds, minutes, hours, days
    event_duration_confidence = Column(Float, nullable=True)
    event_duration_is_explicit = Column(Boolean, nullable=False, default=False)  # Source-stated vs derived

    # Status (Requirement 24)
    status = Column(SQLEnum(CareEventStatus), nullable=False, default=CareEventStatus.UNCONFIRMED, index=True)
    status_reason = Column(Text, nullable=True)
    status_confidence = Column(Float, nullable=True)

    # Severity (Requirements 25, 26) - event-type-specific, never invented
    severity = Column(SQLEnum(CareEventSeverity), nullable=False, default=CareEventSeverity.UNKNOWN)
    severity_confidence = Column(Float, nullable=True)
    severity_details = Column(Text, nullable=True)  # Evidence supporting severity

    # Content & Description
    title = Column(String, nullable=False)  # Brief human-readable title
    description = Column(Text, nullable=True)  # Detailed description
    original_expression = Column(Text, nullable=True)  # Original source wording - NEVER replaced (Req 17, 18)
    normalized_text = Column(Text, nullable=True)  # Normalized structured form

    # Participants (Requirements 30, 31)
    participants = Column(JSONList, nullable=True)  # List of {role, id, name, resolved}
    primary_caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=True)
    primary_provider_id = Column(String, nullable=True)
    facility_id = Column(String, nullable=True)
    location = Column(String, nullable=True)
    location_provenance = Column(String, nullable=True)

    # Evidence & Provenance (Requirements 33, 34)
    evidence_ids = Column(JSONList, nullable=True)  # Evidence.item IDs supporting this event
    primary_evidence_id = Column(String, ForeignKey("evidence.id"), nullable=True, index=True)
    source_ids = Column(JSONList, nullable=True)  # Document IDs, ManualEntry IDs
    extraction_method = Column(String, nullable=True)  # How this event was created
    extraction_version = Column(String, nullable=True)  # Pipeline/prompt/model version
    extraction_confidence = Column(Float, nullable=True)

    # Multi-dimensional Confidence (Requirement 35)
    confidence_event_detection = Column(Float, nullable=True)
    confidence_event_type = Column(Float, nullable=True)
    confidence_subject = Column(Float, nullable=True)
    confidence_temporal = Column(Float, nullable=True)
    confidence_identity = Column(Float, nullable=True)
    confidence_severity = Column(Float, nullable=True)
    confidence_status = Column(Float, nullable=True)
    confidence_outcome = Column(Float, nullable=True)
    confidence_overall = Column(Float, nullable=True)  # Composite
    confidence = synonym("confidence_overall")  # Backward-compatible scalar alias

    # Knowledge Time vs Event Time (Requirement 68)
    knowledge_time = Column(DateTime(timezone=True), nullable=True)  # When SolenOS learned of this event
    documentation_time = Column(DateTime(timezone=True), nullable=True)  # When source was documented
    authoring_time = Column(DateTime(timezone=True), nullable=True)  # When source was authored

    # Lifecycle (Requirements 43, 44, 45, 64)
    lifecycle_stage = Column(SQLEnum(EventLifecycleStage), nullable=False, default=EventLifecycleStage.DETECTED)
    superseded_by_event_id = Column(String, ForeignKey("care_events.id"), nullable=True)
    invalidated_at = Column(DateTime(timezone=True), nullable=True)
    invalidated_by = Column(String, ForeignKey("caregivers.id"), nullable=True)
    invalidation_reason = Column(SQLEnum(EventInvalidationReason), nullable=True)
    invalidation_details = Column(Text, nullable=True)
    corrected_at = Column(DateTime(timezone=True), nullable=True)
    corrected_by = Column(String, ForeignKey("caregivers.id"), nullable=True)
    correction_type = Column(SQLEnum(EventCorrectionType), nullable=True)

    # Recurrence (Requirements 37, 38, 39)
    recurrence_group_id = Column(String, ForeignKey("care_event_recurrence_groups.id", use_alter=True), nullable=True)
    recurrence_sequence = Column(Integer, nullable=True)  # 1, 2, 3...
    recurrence_detection_method = Column(SQLEnum(RecurrenceDetectionMethod), nullable=True)
    recurrence_confidence = Column(Float, nullable=True)
    is_recurrence_representative = Column(Boolean, nullable=False, default=False)  # Representative event for group

    # Grouping (Requirements 48, 49)
    group_id = Column(String, ForeignKey("care_event_groups.id", use_alter=True), nullable=True)
    group_type = Column(SQLEnum(CareEventGroupingType), nullable=True)
    group_role = Column(String, nullable=True)  # e.g., "index_event", "follow_up", "complication"

    # Outcomes (Requirements 52, 53, 54)
    outcome = Column(String, nullable=True)  # Free text outcome
    outcome_type = Column(String, nullable=True)
    outcome_confidence = Column(Float, nullable=True)
    outcome_evidence_ids = Column(JSONList, nullable=True)
    outcome_time = Column(DateTime(timezone=True), nullable=True)
    outcome_time_granularity = Column(SQLEnum(EventTimeGranularity), nullable=True)

    # State Impact (Requirements 55, 56, 57)
    state_impacts = Column(JSONList, nullable=True)  # List of {state_type, impact_type, confidence, evidence_ids}
    state_impact_confidence = Column(Float, nullable=True)

    # Situation linkage (Requirement 59, 98)
    situation_id = Column(String, ForeignKey("situations.id"), nullable=True)
    situation_role = Column(String, nullable=True)

    # Longitudinal record linkage
    longitudinal_record_id = Column(String, ForeignKey("longitudinal_evidence_events.id"), nullable=True)
    longitudinal_evidence_snapshot_id = Column(String, ForeignKey("longitudinal_evidence_snapshots.id"), nullable=True)

    # Change detection signals (Requirement 99)
    change_signals = Column(JSONList, nullable=True)  # {signal_type, strength, evidence_ids}

    # Watch signals (Requirement 100)
    watch_signals = Column(JSONList, nullable=True)  # {signal_type, priority, evidence_ids}

    # Next Best Understanding (Requirement 101)
    unresolved_questions = Column(JSONList, nullable=True)  # List of {question, priority, evidence_ids}

    # Completeness (Requirement 102)
    required_fields_present = Column(Boolean, nullable=False, default=True)
    missing_required_fields = Column(JSONList, nullable=True)

    # Validation (Requirement 103)
    validation_status = Column(String, nullable=False, default="pending")  # pending, passed, failed
    validation_errors = Column(JSONList, nullable=True)

    # Source mention classification (Requirement 75, 76, 77)
    mention_type = Column(SQLEnum(EventMentionType), nullable=False, default=EventMentionType.CURRENT_OCCURRENCE)
    mention_source_event_id = Column(String, ForeignKey("care_events.id"), nullable=True)  # For historical references

    # Processing metadata
    processing_version = Column(String, nullable=True)
    processing_timestamp = Column(DateTime(timezone=True), server_default=func.now())

    # Quality gate (Requirement 79)
    quality_gate_passed = Column(Boolean, nullable=False, default=False)
    quality_gate_details = Column(JSONDict, nullable=True)

    # Summary-specific fields (for care_summary endpoint)
    significance_verdict = Column(String, nullable=True)
    attention_candidate = Column(Boolean, nullable=False, default=False)
    follow_up_candidate = Column(Boolean, nullable=False, default=False)

    # Backward compatibility (legacy fields from pre-canonical CareEvent)
    occurred_at_timezone = Column(String, nullable=True)
    time_provenance = Column(String, nullable=True)
    tags = Column(JSONList, nullable=True, default=list)
    reporter_ids = Column(JSONList, nullable=True, default=list)

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    recorded_at = synonym("created_at")  # Backward-compatible knowledge-time alias
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    created_by_caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=False)

    # Relationships
    subject_person = relationship("Person", foreign_keys=[subject_id])
    person = relationship("Person", foreign_keys=[person_id], back_populates="care_events")
    primary_caregiver = relationship("Caregiver", foreign_keys=[primary_caregiver_id])
    primary_evidence = relationship("Evidence", foreign_keys=[primary_evidence_id])
    superseded_by = relationship("CareEvent", remote_side=[id], foreign_keys=[superseded_by_event_id])
    mentions = relationship("CareEvent", remote_side=[id], foreign_keys=[mention_source_event_id])
    longitudinal_record = relationship("LongitudinalEvidenceEvent", foreign_keys=[longitudinal_record_id])
    longitudinal_snapshot = relationship("LongitudinalEvidenceSnapshot", foreign_keys=[longitudinal_evidence_snapshot_id])

    __table_args__ = (
        Index("ix_care_events_person_type", "person_id", "event_type"),
        Index("ix_care_events_person_time", "person_id", "event_time"),
        Index("ix_care_events_status_type", "status", "event_type"),
        Index("ix_care_events_recurrence_group", "recurrence_group_id"),
        Index("ix_care_events_group", "group_id"),
        Index("ix_care_events_lifecycle", "lifecycle_stage"),
        Index("ix_care_events_mention_type", "mention_type"),
    )


class CareEventVersion(Base):
    """Immutable history of care event changes. Requirements 65, 66."""
    __tablename__ = "care_event_versions"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    care_event_id = Column(String, ForeignKey("care_events.id"), nullable=False, index=True)
    version = Column(Integer, nullable=False)

    # Snapshot of all significant fields
    person_id = Column(String, nullable=False)
    subject_role = Column(SQLEnum(EventSubjectRole), nullable=False)
    subject_id = Column(String, nullable=True)
    subject_label = Column(String, nullable=True)

    event_type = Column(SQLEnum(CareEventType), nullable=False)
    event_subtype = Column(SQLEnum(CareEventSubtype), nullable=True)

    event_time = Column(DateTime(timezone=True), nullable=True)
    event_time_granularity = Column(SQLEnum(EventTimeGranularity), nullable=True)
    event_time_text = Column(String, nullable=True)
    event_time_lower_bound = Column(DateTime(timezone=True), nullable=True)
    event_time_upper_bound = Column(DateTime(timezone=True), nullable=True)

    event_duration = Column(String, nullable=True)
    event_duration_type = Column(SQLEnum(EventDurationType), nullable=True)

    status = Column(SQLEnum(CareEventStatus), nullable=False)
    severity = Column(SQLEnum(CareEventSeverity), nullable=False)

    title = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    original_expression = Column(Text, nullable=False)
    normalized_text = Column(Text, nullable=True)

    participants = Column(JSONList, nullable=True)
    location = Column(String, nullable=True)

    evidence_ids = Column(JSONList, nullable=True)
    primary_evidence_id = Column(String, nullable=True)
    source_ids = Column(JSONList, nullable=True)

    confidence_event_detection = Column(Float, nullable=True)
    confidence_event_type = Column(Float, nullable=True)
    confidence_subject = Column(Float, nullable=True)
    confidence_temporal = Column(Float, nullable=True)
    confidence_identity = Column(Float, nullable=True)
    confidence_severity = Column(Float, nullable=True)
    confidence_status = Column(Float, nullable=True)
    confidence_outcome = Column(Float, nullable=True)
    confidence_overall = Column(Float, nullable=True)

    knowledge_time = Column(DateTime(timezone=True), nullable=True)
    documentation_time = Column(DateTime(timezone=True), nullable=True)
    authoring_time = Column(DateTime(timezone=True), nullable=True)

    lifecycle_stage = Column(SQLEnum(EventLifecycleStage), nullable=False)
    superseded_by_event_id = Column(String, nullable=True)

    recurrence_group_id = Column(String, nullable=True)
    recurrence_sequence = Column(Integer, nullable=True)
    is_recurrence_representative = Column(Boolean, nullable=False, default=False)

    group_id = Column(String, nullable=True)
    group_type = Column(SQLEnum(CareEventGroupingType), nullable=True)
    group_role = Column(String, nullable=True)

    outcome = Column(String, nullable=True)
    outcome_type = Column(String, nullable=True)
    outcome_confidence = Column(Float, nullable=True)
    outcome_evidence_ids = Column(JSONList, nullable=True)
    outcome_time = Column(DateTime(timezone=True), nullable=True)

    state_impacts = Column(JSONList, nullable=True)
    situation_id = Column(String, nullable=True)

    mention_type = Column(SQLEnum(EventMentionType), nullable=False)
    mention_source_event_id = Column(String, nullable=True)

    # Change metadata
    change_type = Column(String, nullable=False)  # correction, supersession, invalidation, update, merge, split
    change_reason = Column(Text, nullable=True)
    changed_by = Column(String, ForeignKey("caregivers.id"), nullable=True)
    changed_by_system = Column(Boolean, nullable=False, default=False)
    affected_downstream = Column(JSONList, nullable=True)  # Track what downstream objects may be stale

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("care_event_id", "version", name="uq_care_event_version"),
        Index("ix_care_event_version_event", "care_event_id", "version"),
    )


class CareEventRelationship(Base):
    """Typed relationships between care events. Requirement 46.

    Critical distinction: Temporal relationships (establishable from dates) vs
    Causal relationships (require stronger evidence, belong to causal reasoning).
    """
    __tablename__ = "care_event_relationships"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))

    # Source and target events
    source_event_id = Column(String, ForeignKey("care_events.id"), nullable=False, index=True)
    target_event_id = Column(String, ForeignKey("care_events.id"), nullable=False, index=True)

    # Relationship type (Requirement 46)
    relationship_type = Column(SQLEnum(CareEventRelationshipType), nullable=False, index=True)

    # Temporal vs Causal distinction (Requirement 47)
    is_temporal = Column(Boolean, nullable=False, default=True)
    is_causal = Column(Boolean, nullable=False, default=False)
    causal_evidence_ids = Column(JSONList, nullable=True)  # Evidence specifically supporting causality

    # Provenance
    created_by = Column(String, ForeignKey("caregivers.id"), nullable=True)
    created_by_system = Column(Boolean, nullable=False, default=False)
    detection_method = Column(String, nullable=True)  # How this relationship was found
    detection_confidence = Column(Float, nullable=True)

    # Temporal context (Requirement 53)
    temporal_relationship = Column(String, nullable=True)  # BEFORE, AFTER, DURING, OVERLAPS, etc.
    time_gap_seconds = Column(Float, nullable=True)  # Time between events if applicable

    # Strength & Confidence
    strength = Column(Float, nullable=True)  # 0-1 relationship strength
    confidence = Column(Float, nullable=True)

    # Evidence
    evidence_ids = Column(JSONList, nullable=True)  # Evidence supporting this relationship

    # Status
    is_active = Column(Boolean, nullable=False, default=True)
    invalidated_at = Column(DateTime(timezone=True), nullable=True)
    invalidated_by = Column(String, ForeignKey("caregivers.id"), nullable=True)
    invalidation_reason = Column(Text, nullable=True)

    # Resolution status for causal claims
    causal_resolution_status = Column(String, nullable=True)  # proposed, supported, contradicted, refuted

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    source_event = relationship("CareEvent", foreign_keys=[source_event_id], backref="outgoing_relationships")
    target_event = relationship("CareEvent", foreign_keys=[target_event_id], backref="incoming_relationships")
    creator = relationship("Caregiver", foreign_keys=[created_by])
    invalidator = relationship("Caregiver", foreign_keys=[invalidated_by])

    __table_args__ = (
        UniqueConstraint("source_event_id", "target_event_id", "relationship_type", name="uq_care_event_relationship"),
        Index("ix_care_event_rel_source_type", "source_event_id", "relationship_type"),
        Index("ix_care_event_rel_target_type", "target_event_id", "relationship_type"),
        Index("ix_care_event_rel_temporal_causal", "is_temporal", "is_causal"),
    )


class CareEventRecurrenceGroup(Base):
    """Recurrence group - higher-order representation of repeated events. Requirement 37, 38.

    Does NOT replace individual event identities.
    """
    __tablename__ = "care_event_recurrence_groups"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)

    # Group characterization
    event_type = Column(SQLEnum(CareEventType), nullable=False, index=True)
    event_subtype = Column(SQLEnum(CareEventSubtype), nullable=True)

    # Temporal pattern
    first_occurrence_time = Column(DateTime(timezone=True), nullable=True)
    last_occurrence_time = Column(DateTime(timezone=True), nullable=True)
    occurrence_count = Column(Integer, nullable=False, default=0)

    # Pattern analysis
    pattern_type = Column(String, nullable=True)  # regular, irregular, clustered, increasing, decreasing
    average_interval_days = Column(Float, nullable=True)
    interval_std_dev_days = Column(Float, nullable=True)
    frequency_per_month = Column(Float, nullable=True)
    trend = Column(String, nullable=True)  # increasing, decreasing, stable

    # Confidence
    pattern_confidence = Column(Float, nullable=True)
    grouping_confidence = Column(Float, nullable=True)

    # Detection
    detection_method = Column(SQLEnum(RecurrenceDetectionMethod), nullable=True)
    detection_evidence_ids = Column(JSONList, nullable=True)

    # Status
    is_active = Column(Boolean, nullable=False, default=True)
    status = Column(String, nullable=False, default="active")  # active, resolved, monitoring

    # Representative event
    representative_event_id = Column(String, ForeignKey("care_events.id", use_alter=True), nullable=True)
    member_event_ids = Column(JSONList, nullable=True)

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    person = relationship("Person", foreign_keys=[person_id])
    representative_event = relationship("CareEvent", foreign_keys=[representative_event_id])

    __table_args__ = (
        Index("ix_recurrence_group_person_type", "person_id", "event_type"),
        Index("ix_recurrence_group_active", "is_active"),
    )


class CareEventGroup(Base):
    """Event grouping into meaningful structures. Requirement 48, 49.

    Does NOT erase individual event identities.
    """
    __tablename__ = "care_event_groups"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)

    # Group characterization
    group_type = Column(SQLEnum(CareEventGroupingType), nullable=False, index=True)
    title = Column(String, nullable=False)
    description = Column(Text, nullable=True)

    # Temporal bounds
    temporal_start = Column(DateTime(timezone=True), nullable=True)
    temporal_end = Column(DateTime(timezone=True), nullable=True)

    # Member events
    member_event_ids = Column(JSONList, nullable=True)
    member_count = Column(Integer, nullable=False, default=0)

    # Confidence
    grouping_confidence = Column(Float, nullable=True)
    grouping_reason = Column(Text, nullable=True)

    # Status
    is_active = Column(Boolean, nullable=False, default=True)
    status = Column(String, nullable=False, default="active")  # active, resolved, monitoring

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    person = relationship("Person", foreign_keys=[person_id])

    __table_args__ = (
        Index("ix_care_event_group_person_type", "person_id", "group_type"),
        Index("ix_care_event_group_active", "is_active"),
    )


class CareEventCorrection(Base):
    """Event correction with full history preservation. Requirements 43, 65, 66."""
    __tablename__ = "care_event_corrections"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    care_event_id = Column(String, ForeignKey("care_events.id"), nullable=False, index=True)

    # What was corrected
    correction_type = Column(SQLEnum(EventCorrectionType), nullable=False)

    # Before/after values
    field_name = Column(String, nullable=False)
    old_value = Column(Text, nullable=True)
    new_value = Column(Text, nullable=True)

    # Provenance
    corrected_by = Column(String, ForeignKey("caregivers.id"), nullable=False)
    correction_reason = Column(Text, nullable=True)
    correction_evidence_ids = Column(JSONList, nullable=True)

    # Source vs evidence correction distinction (similar to EvidenceCorrection)
    is_source_correction = Column(Boolean, nullable=False, default=False)
    source_correction_details = Column(Text, nullable=True)

    # Downstream impact tracking (Requirement 43, 133)
    affected_relationships = Column(JSONList, nullable=True)
    affected_recurrence_groups = Column(JSONList, nullable=True)
    affected_groups = Column(JSONList, nullable=True)
    affected_situations = Column(JSONList, nullable=True)
    affected_state_impacts = Column(JSONList, nullable=True)
    affected_change_signals = Column(JSONList, nullable=True)
    affected_watches = Column(JSONList, nullable=True)
    affected_summaries = Column(JSONList, nullable=True)

    # Status
    applied = Column(Boolean, nullable=False, default=False)
    applied_at = Column(DateTime(timezone=True), nullable=True)
    application_details = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_care_event_correction_event", "care_event_id"),
        Index("ix_care_event_correction_applied", "applied"),
    )


class CareEventInvalidation(Base):
    """Event invalidation with downstream propagation tracking. Requirements 44, 107, 134."""
    __tablename__ = "care_event_invalidations"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    care_event_id = Column(String, ForeignKey("care_events.id"), nullable=False, index=True)

    # Reason
    reason = Column(SQLEnum(EventInvalidationReason), nullable=False)
    reason_details = Column(Text, nullable=True)

    # Who invalidated
    invalidated_by = Column(String, ForeignKey("caregivers.id"), nullable=False)

    # Downstream propagation (Requirement 107, 134)
    propagation_status = Column(String, nullable=False, default="pending")  # pending, in_progress, completed, failed
    affected_relationships = Column(JSONList, nullable=True)
    affected_recurrence_groups = Column(JSONList, nullable=True)
    affected_groups = Column(JSONList, nullable=True)
    affected_situations = Column(JSONList, nullable=True)
    affected_state_impacts = Column(JSONList, nullable=True)
    affected_change_signals = Column(JSONList, nullable=True)
    affected_watches = Column(JSONList, nullable=True)
    affected_summaries = Column(JSONList, nullable=True)
    affected_next_actions = Column(JSONList, nullable=True)

    # Propagation log
    propagation_log = Column(JSONList, nullable=True)  # [{step, target_type, target_id, status, timestamp, details}]

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_care_event_invalidation_event", "care_event_id"),
        Index("ix_care_event_invalidation_propagation", "propagation_status"),
    )


class CareEventSupersession(Base):
    """Event supersession with historical preservation. Requirement 45.

    SUPERSEDED: valid at one time but no longer current.
    INVALIDATED: unreliable/erroneous.
    """
    __tablename__ = "care_event_supersessions"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))

    # The event being superseded (historical)
    superseded_event_id = Column(String, ForeignKey("care_events.id"), nullable=False, index=True)

    # The superseding event (current)
    superseding_event_id = Column(String, ForeignKey("care_events.id"), nullable=False, index=True)

    # Reason
    reason = Column(SQLEnum(EventSupersessionReason), nullable=False)
    reason_details = Column(Text, nullable=True)

    # Who established supersession
    established_by = Column(String, ForeignKey("caregivers.id"), nullable=True)
    established_by_system = Column(Boolean, nullable=False, default=False)

    # Validity
    effective_from = Column(DateTime(timezone=True), nullable=False)
    is_explicit = Column(Boolean, nullable=False, default=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("superseded_event_id", "superseding_event_id", name="uq_care_event_supersession"),
        Index("ix_care_event_supersession_superseding", "superseding_event_id"),
    )


class CareEventMentionDeduplication(Base):
    """Event mention deduplication without information loss. Requirements 40, 41, 42.

    Multiple source mentions -> same underlying event.
    Preserves all evidence, links via SAME_EVENT or POSSIBLE_DUPLICATE.
    """
    __tablename__ = "care_event_mention_deduplications"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))

    # The event mentions that may represent the same event
    mention_a_id = Column(String, ForeignKey("care_events.id"), nullable=False, index=True)
    mention_b_id = Column(String, ForeignKey("care_events.id"), nullable=False, index=True)

    # Deduplication metadata
    similarity_score = Column(Float, nullable=True)  # 0-1 confidence
    match_criteria = Column(JSONList, nullable=True)  # Which criteria matched: subject, type, time, context, participants
    match_details = Column(JSONDict, nullable=True)  # Detailed match breakdown

    # Status (Requirement 42)
    status = Column(SQLEnum(EventMentionDeduplicationStatus), nullable=False, default=EventMentionDeduplicationStatus.UNRESOLVED, index=True)
    confirmed_by = Column(String, ForeignKey("caregivers.id"), nullable=True)

    # Resolution
    resolved_event_id = Column(String, ForeignKey("care_events.id"), nullable=True)  # The canonical event if merged
    resolution_reason = Column(Text, nullable=True)

    # Both mentions remain - this is just a link
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    confirmed_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("mention_a_id", "mention_b_id", name="uq_care_event_mention_deduplication"),
        Index("ix_care_event_dedup_status", "status"),
    )


class CareEventStateImpact(Base):
    """Explicit contract for event-to-state impact. Requirements 55, 56, 57, 96, 97."""
    __tablename__ = "care_event_state_impacts"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    care_event_id = Column(String, ForeignKey("care_events.id"), nullable=False, index=True)

    # Target state
    state_type = Column(String, nullable=False, index=True)  # e.g., "mobility", "cognition", "medication_adherence"
    state_subject_id = Column(String, nullable=True)  # Specific subject if different from event subject
    state_subject_role = Column(SQLEnum(EventSubjectRole), nullable=False, default=EventSubjectRole.CARE_RECIPIENT)

    # Impact characterization
    impact_type = Column(SQLEnum(EventStateImpactType), nullable=False)
    impact_confidence = Column(SQLEnum(EventStateImpactConfidence), nullable=False, default=EventStateImpactConfidence.UNKNOWN)
    impact_details = Column(JSONDict, nullable=True)  # Structured impact description

    # Evidence & Provenance (Requirement 57)
    supporting_evidence_ids = Column(JSONList, nullable=True)
    supporting_care_event_ids = Column(JSONList, nullable=True)  # Other events supporting this impact
    reasoning_rule = Column(String, nullable=True)  # e.g., "fall->mobility_decline", "med_change->symptom_change"
    reasoning_confidence = Column(Float, nullable=True)

    # Temporal relationship
    temporal_relationship = Column(String, nullable=True)  # IMMEDIATELY_AFTER, DELAYED, CONCOMITANT
    time_lag_days = Column(Float, nullable=True)

    # Validation
    validated_by_state_engine = Column(Boolean, nullable=False, default=False)
    state_engine_validation_time = Column(DateTime(timezone=True), nullable=True)
    state_engine_validation_notes = Column(Text, nullable=True)

    # Reversibility (Requirement 97)
    is_revoked = Column(Boolean, nullable=False, default=False)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    revoked_by = Column(String, ForeignKey("caregivers.id"), nullable=True)
    revocation_reason = Column(Text, nullable=True)
    superseded_by_impact_id = Column(String, ForeignKey("care_event_state_impacts.id", use_alter=True), nullable=True)

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    care_event = relationship("CareEvent", foreign_keys=[care_event_id], backref="state_impact_records")
    superseded_by = relationship("CareEventStateImpact", remote_side=[id], foreign_keys=[superseded_by_impact_id])

    __table_args__ = (
        Index("ix_care_event_state_impact_event", "care_event_id"),
        Index("ix_care_event_state_impact_state_type", "state_type"),
        Index("ix_care_event_state_impact_confidence", "impact_confidence"),
    )


# Update Person model to add back-reference
# In app/models/care.py, Person class should have:
# care_events = relationship("CareEvent", foreign_keys="CareEvent.person_id", back_populates="person")


class CaptureIdempotency(Base):
    """Persisted idempotency state for retried situation-capture requests.

    A key is scoped to the authenticated person/caregiver pair. The request
    fingerprint prevents accidental reuse of a key for different content, and
    the stored response makes retries return the original persisted record IDs.
    Incomplete rows also prevent concurrent retries from processing twice.
    """

    __tablename__ = "capture_idempotency"
    __table_args__ = (
        UniqueConstraint(
            "person_id",
            "caregiver_id",
            "idempotency_key",
            name="uq_capture_idempotency_scope_key",
        ),
        Index("ix_capture_idempotency_expires_at", "expires_at"),
    )

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=False, index=True)
    idempotency_key = Column(String(255), nullable=False)
    request_hash = Column(String(64), nullable=False)
    response_status = Column(Integer, nullable=True)
    response_json = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)

