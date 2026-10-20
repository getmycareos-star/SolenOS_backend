from sqlalchemy import Column, String, DateTime, Date, Boolean, Text, ForeignKey, Float, Integer, UniqueConstraint, Index
from sqlalchemy.sql import func
import uuid
from app.core.database import Base
from app.core.types import JSONList, JSONDict
from sqlalchemy.orm import relationship, synonym

from app.core.evidence_enums import (
    EvidenceType,
    EvidenceStatus,
    EvidenceSubjectRole,
    EvidenceObserverRole,
    EvidenceAuthorRole,
    EvidenceRelationshipType,
    ConfidenceDimension,
    TemporalGranularity,
    TemporalAnchorType,
    SourceType,
    ExtractionMethod,
    EntityResolutionStatus,
    NegationStatus,
    NormalizationType,
    CorrectionType,
    InvalidationReason,
    SupersessionReason,
    UncertaintyLevel,
    HedgeType,
)


class Evidence(Base):
    __tablename__ = "evidence"
    __table_args__ = (
        Index("ix_evidence_person_id", "person_id"),
        Index("ix_evidence_uploaded_by", "uploaded_by_caregiver_id"),
        Index("ix_evidence_source_id", "source_id"),
        Index("ix_evidence_type", "type"),
        Index("ix_evidence_status", "evidence_status"),
        Index("ix_evidence_created_at", "created_at"),
        Index("ix_evidence_uploaded_at", "uploaded_at"),
        Index("ix_evidence_content_hash", "content_hash"),
    )

    # Primary key
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    
    # Person reference
    person_id = Column(String, ForeignKey("persons.id"), nullable=False)
    
    # Subject details (from rich model)
    subject_role = Column(String, nullable=False, default=EvidenceSubjectRole.OTHER.value)  # Safe when subject is unknown
    subject_id = Column(String, ForeignKey("persons.id"), nullable=True)  # FK to persons.id or other entities
    subject_identifier = Column(String, nullable=True)  # external identifier
    entity_resolution_status = Column(String, nullable=False, default=EntityResolutionStatus.UNRESOLVED.value)  # Do not imply an unverified match
    entity_resolution_confidence = Column(Float, nullable=True)
    
    # Observer details (from rich model)
    observer_role = Column(String, nullable=True)  # EvidenceObserverRole
    observer_id = Column(String, nullable=True)
    observer_name = Column(String, nullable=True)
    
    # Author details (from rich model)
    author_role = Column(String, nullable=True)  # EvidenceAuthorRole
    author_id = Column(String, nullable=True)
    author_name = Column(String, nullable=True)
    
    # Uploader
    uploaded_by_caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=False)
    
    # Evidence core (from both models)
    type = Column(String, nullable=False)  # EvidenceType
    subtype = Column(String, nullable=True)
    source_text = Column(Text, nullable=False)
    
    # Simple model columns (backward compatibility)
    original_file_path = Column(String, nullable=True)
    uploaded_at = Column(DateTime(timezone=True), server_default=func.now())
    extra_metadata = Column(Text, nullable=True)
    time_provenance = Column(String, nullable=True)
    location = Column(String, nullable=True)
    confidence = Column(Float, nullable=True)  # single confidence for backward compatibility
    evidence_status = Column(String, nullable=False, default="active")  # EvidenceStatus
    # Backward-compatible mapped alias used by the evidence service and schemas.
    status = synonym("evidence_status")
    source_type = Column(String, nullable=True)  # SourceType
    source_span_start = Column(Integer, nullable=True)
    source_span_end = Column(Integer, nullable=True)
    
    # === ADDITIONAL CONFIDENCE DIMENSIONS ===
    # Confidence in source reliability (how trustworthy is the source)
    confidence_source_reliability = Column(Float, nullable=True)
    # Confidence in collector expertise (how knowledgeable is the collector)
    confidence_collector_expertise = Column(Float, nullable=True)
    # Confidence in environmental conditions during collection
    confidence_collection_conditions = Column(Float, nullable=True)
    
    # === MORE DETAILED TEMPORAL FIELDS ===
    # Duration of the event/evidence
    duration_value = Column(Float, nullable=True)  # Numeric duration
    duration_unit = Column(String, nullable=True)  # seconds, minutes, hours, days, etc.
    duration_is_exact = Column(Boolean, nullable=True)  # Whether duration is exact or estimated
    # Frequency/recurrence information
    frequency_value = Column(Float, nullable=True)  # How often it occurs
    frequency_unit = Column(String, nullable=True)  # per second, minute, hour, day, etc.
    frequency_pattern = Column(String, nullable=True)  # e.g., "daily", "weekly", "irregular"
    recurrence_pattern = Column(JSONDict, nullable=True)  # Detailed recurrence pattern
    
    # === RICH MODEL COLUMNS: normalized text ===
    normalized_text = Column(Text, nullable=True)
    normalization_type = Column(String, nullable=True)  # NormalizationType
    normalization_details = Column(Text, nullable=True)  # JSON stored as text
    
    # Negation, uncertainty, hedging
    negation_status = Column(String, nullable=False, default="asserted")  # NegationStatus
    uncertainty_level = Column(String, nullable=False, default="certain")  # UncertaintyLevel
    hedge_type = Column(String, nullable=True)  # HedgeType
    hedge_text = Column(String, nullable=True)
    
    # === TEMPORAL INFORMATION (rich model) ===
    event_time = Column(DateTime(timezone=True), nullable=True)
    event_time_granularity = Column(String, nullable=True)  # TemporalGranularity
    event_time_text = Column(String, nullable=True)
    event_time_anchor = Column(String, nullable=True)  # TemporalAnchorType
    event_time_anchor_reference = Column(String, nullable=True)
    observation_time = Column(DateTime(timezone=True), nullable=True)
    observation_time_granularity = Column(String, nullable=True)  # TemporalGranularity
    observation_time_text = Column(String, nullable=True)
    document_time = Column(DateTime(timezone=True), nullable=True)
    authoring_time = Column(DateTime(timezone=True), nullable=True)
    
    # === ENHANCED SOURCE PROVENANCE ===
    source_id = Column(String, ForeignKey("documents.id"), nullable=True)
    source_version = Column(Integer, nullable=True)
    source_type_col = Column(String, nullable=True)  # SourceType (renamed to avoid conflict)
    
    # Chain of custody tracking
    chain_of_custody = Column(JSONList, nullable=True)  # List of custodians with timestamps
    handling_procedures = Column(JSONDict, nullable=True)  # Procedures followed during handling
    storage_conditions = Column(JSONDict, nullable=True)  # Storage conditions (temperature, humidity, etc.)
    transport_method = Column(String, nullable=True)  # How evidence was transported
    
    # Source location details
    source_location_page = Column(Integer, nullable=True)
    source_location_paragraph = Column(Integer, nullable=True)
    source_location_text_span_start = Column(Integer, nullable=True)
    source_location_text_span_end = Column(Integer, nullable=True)
    source_location_image_region = Column(Text, nullable=True)  # JSON stored as text
    source_location_structured_field = Column(String, nullable=True)
    
    # === RICH EXTRACTION METADATA ===
    extraction_method = Column(String, nullable=True)  # ExtractionMethod
    extraction_version = Column(String, nullable=True)
    extraction_model = Column(String, nullable=True)
    extraction_model_version = Column(String, nullable=True)  # Specific version of the model
    extraction_parameters = Column(JSONDict, nullable=True)  # Model parameters used
    preprocessing_steps = Column(JSONList, nullable=True)  # Steps taken before extraction
    postprocessing_steps = Column(JSONList, nullable=True)  # Steps taken after extraction
    extraction_confidence = Column(Float, nullable=True)
    extraction_timestamp = Column(DateTime(timezone=True), nullable=True)
    
    # === CONFIDENCE DIMENSIONS (rich model) ===
    confidence_extraction = Column(Float, nullable=True)
    confidence_entity_resolution = Column(Float, nullable=True)
    confidence_temporal = Column(Float, nullable=True)
    confidence_source_support = Column(Float, nullable=True)
    confidence_interpretation = Column(Float, nullable=True)
    
    # === STATUS AND VALIDITY (rich model) ===
    status_reason = Column(String, nullable=True)
    valid_from = Column(DateTime(timezone=True), nullable=True)
    valid_until = Column(DateTime(timezone=True), nullable=True)
    
    # === PROCESSING LINEAGE ===
    # Track which processes have modified this evidence
    processing_history = Column(JSONList, nullable=True)  # List of processing steps with timestamps
    processing_version = Column(String, nullable=True)
    last_processed_by = Column(String, ForeignKey("accounts.id"), nullable=True)  # Who last processed it
    last_processed_at = Column(DateTime(timezone=True), nullable=True)
    
    # === QUALITY GATE ===
    quality_gate_passed = Column(Boolean, nullable=True)
    quality_gate_details = Column(Text, nullable=True)  # JSON stored as text
    quality_gate_rules = Column(JSONList, nullable=True)  # Detailed validation rules applied
    quality_gate_thresholds = Column(JSONDict, nullable=True)  # Confidence thresholds used
    
    # === VERSIONING FOR OPTIMISTIC LOCKING / AUDIT ===
    version = Column(Integer, nullable=False, default=1)
    
    # === USER-DEFINED CUSTOM FIELDS ===
    # For extensibility without schema changes
    custom_fields = Column(JSONDict, nullable=True)  # Arbitrary key-value pairs
    
    # === INTEGRATION WITH EXTERNAL EVIDENCE STANDARDS ===
    # Mapping to external standards
    iso_standard_references = Column(JSONList, nullable=True)  # References to ISO standards
    legal_evidence_format = Column(String, nullable=True)  # e.g., "FRE 702", "Daubert"
    external_evidence_ids = Column(JSONList, nullable=True)  # IDs in external systems
    
    # === EVIDENCE SIMILARITY/SEARCH FEATURES ===
    content_hash = Column(String, nullable=True)  # Hash of content for duplicate detection
    feature_vector = Column(Text, nullable=True)  # Serialized feature vector for similarity search
    
    # === TIMESTAMPS ===
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # === RELATIONSHIPS ===
    # Person reference relationship
    person = relationship("Person", foreign_keys=[person_id])
    # Subject person relationship (when subject_id refers to a person)
    subject_person = relationship("Person", foreign_keys=[subject_id])
    # Last processed by relationship
    last_processed_by_rel = relationship("Account", foreign_keys=[last_processed_by])


class EvidenceRelationship(Base):
    """Typed, reversible relationship between two evidence records."""

    __tablename__ = "evidence_relationships"
    __table_args__ = (
        UniqueConstraint(
            "source_evidence_id",
            "target_evidence_id",
            "relationship_type",
            name="uq_evidence_relationship_pair_type",
        ),
        Index("ix_evidence_relationship_source", "source_evidence_id", "is_active"),
        Index("ix_evidence_relationship_target", "target_evidence_id", "is_active"),
    )

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    source_evidence_id = Column(
        String, ForeignKey("evidence.id", ondelete="CASCADE"), nullable=False
    )
    target_evidence_id = Column(
        String, ForeignKey("evidence.id", ondelete="CASCADE"), nullable=False
    )
    relationship_type = Column(String, nullable=False)
    temporal_relationship = Column(String, nullable=True)
    detection_method = Column(String, nullable=True)
    detection_confidence = Column(Float, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    created_by = Column(String, ForeignKey("caregivers.id"), nullable=True)
    created_by_system = Column(Boolean, nullable=False, default=False)

    source_evidence = relationship("Evidence", foreign_keys=[source_evidence_id])
    target_evidence = relationship("Evidence", foreign_keys=[target_evidence_id])


class EvidenceVersion(Base):
    """Immutable snapshot of evidence fields before/after a correction."""

    __tablename__ = "evidence_versions"
    __table_args__ = (
        UniqueConstraint("evidence_id", "version", name="uq_evidence_version_number"),
        Index("ix_evidence_versions_evidence", "evidence_id", "version"),
    )

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    evidence_id = Column(
        String, ForeignKey("evidence.id", ondelete="CASCADE"), nullable=False
    )
    version = Column(Integer, nullable=False)

    # Evidence content and provenance snapshot.
    subject_role = Column(String, nullable=False)
    subject_id = Column(String, nullable=True)
    observer_role = Column(String, nullable=True)
    observer_id = Column(String, nullable=True)
    author_role = Column(String, nullable=True)
    author_id = Column(String, nullable=True)
    type = Column(String, nullable=False)
    subtype = Column(String, nullable=True)
    source_text = Column(Text, nullable=False)
    normalized_text = Column(Text, nullable=True)
    normalization_type = Column(String, nullable=True)
    normalization_details = Column(Text, nullable=True)
    negation_status = Column(String, nullable=False)
    uncertainty_level = Column(String, nullable=False)
    hedge_type = Column(String, nullable=True)
    hedge_text = Column(String, nullable=True)

    # Temporal and source lineage snapshot.
    event_time = Column(DateTime(timezone=True), nullable=True)
    event_time_granularity = Column(String, nullable=True)
    event_time_text = Column(String, nullable=True)
    event_time_anchor = Column(String, nullable=True)
    observation_time = Column(DateTime(timezone=True), nullable=True)
    document_time = Column(DateTime(timezone=True), nullable=True)
    source_id = Column(String, nullable=True)
    source_version = Column(Integer, nullable=True)
    source_type = Column(String, nullable=True)
    source_location_page = Column(Integer, nullable=True)
    source_location_paragraph = Column(Integer, nullable=True)
    source_location_text_span_start = Column(Integer, nullable=True)
    source_location_text_span_end = Column(Integer, nullable=True)

    # Extraction confidence and validity snapshot.
    extraction_method = Column(String, nullable=True)
    extraction_version = Column(String, nullable=True)
    extraction_model = Column(String, nullable=True)
    confidence_extraction = Column(Float, nullable=True)
    confidence_entity_resolution = Column(Float, nullable=True)
    confidence_temporal = Column(Float, nullable=True)
    confidence_source_support = Column(Float, nullable=True)
    confidence_interpretation = Column(Float, nullable=True)
    status = Column(String, nullable=False)
    status_reason = Column(Text, nullable=True)
    valid_from = Column(DateTime(timezone=True), nullable=True)
    valid_until = Column(DateTime(timezone=True), nullable=True)

    # Audit metadata. Snapshots are append-only in the service layer.
    change_type = Column(String, nullable=False)
    change_reason = Column(Text, nullable=True)
    changed_by = Column(String, nullable=True)
    changed_by_system = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class EvidenceCorrection(Base):
    """Auditable correction proposal and its downstream propagation state."""

    __tablename__ = "evidence_corrections"
    __table_args__ = (
        Index("ix_evidence_corrections_evidence", "evidence_id", "created_at"),
        Index("ix_evidence_corrections_applied", "applied"),
    )

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    evidence_id = Column(
        String, ForeignKey("evidence.id", ondelete="CASCADE"), nullable=False
    )
    correction_type = Column(String, nullable=False)
    field_name = Column(String, nullable=False)
    old_value = Column(Text, nullable=True)
    new_value = Column(Text, nullable=True)
    correction_reason = Column(Text, nullable=True)
    is_source_correction = Column(Boolean, nullable=False, default=False)
    source_correction_details = Column(Text, nullable=True)
    affected_claims = Column(JSONList, nullable=False, default=list)
    affected_events = Column(JSONList, nullable=False, default=list)
    affected_care_states = Column(JSONList, nullable=False, default=list)
    affected_situations = Column(JSONList, nullable=False, default=list)
    affected_watches = Column(JSONList, nullable=False, default=list)
    affected_summaries = Column(JSONList, nullable=False, default=list)
    applied = Column(Boolean, nullable=False, default=False)
    applied_at = Column(DateTime(timezone=True), nullable=True)
    application_details = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    corrected_by = Column(String, ForeignKey("caregivers.id"), nullable=False)


class EvidenceInvalidation(Base):
    """Invalidation record that preserves why evidence is no longer current."""

    __tablename__ = "evidence_invalidations"
    __table_args__ = (
        Index("ix_evidence_invalidations_evidence", "evidence_id", "created_at"),
        Index("ix_evidence_invalidations_status", "propagation_status"),
    )

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    evidence_id = Column(
        String, ForeignKey("evidence.id", ondelete="CASCADE"), nullable=False
    )
    reason = Column(String, nullable=False)
    reason_details = Column(Text, nullable=True)
    propagation_status = Column(String, nullable=False, default="pending")
    affected_claims = Column(JSONList, nullable=False, default=list)
    affected_events = Column(JSONList, nullable=False, default=list)
    affected_care_states = Column(JSONList, nullable=False, default=list)
    affected_situations = Column(JSONList, nullable=False, default=list)
    affected_watches = Column(JSONList, nullable=False, default=list)
    affected_summaries = Column(JSONList, nullable=False, default=list)
    affected_next_actions = Column(JSONList, nullable=False, default=list)
    propagation_log = Column(JSONList, nullable=False, default=list)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    invalidated_by = Column(String, ForeignKey("caregivers.id"), nullable=False)


class EvidenceSupersession(Base):
    """Explicit lineage from an older evidence item to its replacement."""

    __tablename__ = "evidence_supersessions"
    __table_args__ = (
        UniqueConstraint(
            "superseded_evidence_id",
            "superseding_evidence_id",
            name="uq_evidence_supersession_pair",
        ),
        Index("ix_evidence_supersessions_old", "superseded_evidence_id"),
        Index("ix_evidence_supersessions_new", "superseding_evidence_id"),
    )

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    superseded_evidence_id = Column(
        String, ForeignKey("evidence.id", ondelete="CASCADE"), nullable=False
    )
    superseding_evidence_id = Column(
        String, ForeignKey("evidence.id", ondelete="CASCADE"), nullable=False
    )
    reason = Column(String, nullable=False)
    reason_details = Column(Text, nullable=True)
    effective_from = Column(DateTime(timezone=True), nullable=False)
    is_explicit = Column(Boolean, nullable=False, default=True)
    established_by = Column(String, ForeignKey("caregivers.id"), nullable=True)
    established_by_system = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class EvidenceDeduplication(Base):
    """Reviewable duplicate candidate; evidence records remain distinct."""

    __tablename__ = "evidence_deduplications"
    __table_args__ = (
        UniqueConstraint("evidence_id_a", "evidence_id_b", name="uq_evidence_dedup_pair"),
        Index("ix_evidence_dedup_status", "status"),
    )

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    evidence_id_a = Column(
        String, ForeignKey("evidence.id", ondelete="CASCADE"), nullable=False
    )
    evidence_id_b = Column(
        String, ForeignKey("evidence.id", ondelete="CASCADE"), nullable=False
    )
    similarity_score = Column(Float, nullable=True)
    match_type = Column(String, nullable=False)
    matched_fields = Column(JSONList, nullable=False, default=list)
    status = Column(String, nullable=False, default="pending")
    confirmed_by = Column(String, ForeignKey("caregivers.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    confirmed_at = Column(DateTime(timezone=True), nullable=True)
