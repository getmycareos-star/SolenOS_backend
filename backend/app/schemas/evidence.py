"""Pydantic schemas for Evidence API."""

from pydantic import BaseModel, Field, ConfigDict
from datetime import datetime
from typing import Optional, List, Dict, Any, Literal
from app.core.evidence_enums import (
    EvidenceType,
    EvidenceStatus,
    EvidenceSubjectRole,
    EvidenceObserverRole,
    EvidenceAuthorRole,
    ConfidenceDimension,
    TemporalGranularity,
    TemporalAnchorType,
    SourceType,
    NormalizationType,
    ExtractionMethod,
    EvidenceRelationshipType,
    EntityResolutionStatus,
    NegationStatus,
    UncertaintyLevel,
    HedgeType,
    CorrectionType,
    InvalidationReason,
    SupersessionReason,
)


class SourceLocation(BaseModel):
    """Precise source location within a document."""
    page: Optional[int] = None
    paragraph: Optional[int] = None
    text_span_start: Optional[int] = None
    text_span_end: Optional[int] = None
    image_region: Optional[Dict[str, Any]] = None  # {x, y, width, height}
    structured_field: Optional[str] = None


class ConfidenceDimensions(BaseModel):
    """Multi-dimensional confidence scores."""
    extraction: Optional[float] = Field(None, ge=0.0, le=1.0)
    entity_resolution: Optional[float] = Field(None, ge=0.0, le=1.0)
    temporal: Optional[float] = Field(None, ge=0.0, le=1.0)
    source_support: Optional[float] = Field(None, ge=0.0, le=1.0)
    interpretation: Optional[float] = Field(None, ge=0.0, le=1.0)
    overall: Optional[float] = Field(None, ge=0.0, le=1.0)


class TemporalInfo(BaseModel):
    """Structured temporal information."""
    event_time: Optional[datetime] = None
    event_time_granularity: Optional[TemporalGranularity] = None
    event_time_text: Optional[str] = None
    event_time_anchor: Optional[TemporalAnchorType] = None
    event_time_anchor_reference: Optional[str] = None
    observation_time: Optional[datetime] = None
    observation_time_granularity: Optional[TemporalGranularity] = None
    observation_time_text: Optional[str] = None
    document_time: Optional[datetime] = None
    authoring_time: Optional[datetime] = None


class NormalizationInfo(BaseModel):
    """Normalization preserving original expression."""
    normalized_text: Optional[str] = None
    normalization_type: Optional[NormalizationType] = None
    normalization_details: Optional[Dict[str, Any]] = None
    original_expression: str  # REQUIRED - never replace raw expression


class ExtractionMetadata(BaseModel):
    """Extraction method and version tracking."""
    extraction_method: Optional[ExtractionMethod] = None
    extraction_version: Optional[str] = None
    extraction_model: Optional[str] = None
    extraction_confidence: Optional[float] = Field(None, ge=0.0, le=1.0)
    extraction_timestamp: Optional[datetime] = None


class QualityGateResult(BaseModel):
    """Quality gate validation result."""
    passed: bool
    details: Optional[Dict[str, Any]] = None
    failed_checks: List[str] = []


class EvidenceBase(BaseModel):
    """Base evidence schema with all core fields."""
    # Subject (Requirement 7)
    subject_role: EvidenceSubjectRole = EvidenceSubjectRole.CARE_RECIPIENT
    subject_id: Optional[str] = None
    subject_identifier: Optional[str] = None
    entity_resolution_status: EntityResolutionStatus = EntityResolutionStatus.UNRESOLVED
    entity_resolution_confidence: Optional[float] = Field(None, ge=0.0, le=1.0)

    # Observer (Requirement 8)
    observer_role: Optional[EvidenceObserverRole] = None
    observer_id: Optional[str] = None
    observer_name: Optional[str] = None

    # Author (Requirement 9)
    author_role: Optional[EvidenceAuthorRole] = None
    author_id: Optional[str] = None
    author_name: Optional[str] = None

    # Uploader
    uploaded_by_caregiver_id: str

    # Type (Requirements 5, 6)
    type: EvidenceType
    subtype: Optional[str] = None

    # Content (Requirements 3, 11)
    source_text: str
    normalization: Optional[NormalizationInfo] = None

    # Negation, uncertainty, hedging (Requirements 47, 48, 49)
    negation_status: NegationStatus = NegationStatus.ASSERTED
    uncertainty_level: UncertaintyLevel = UncertaintyLevel.CERTAIN
    hedge_type: Optional[HedgeType] = None
    hedge_text: Optional[str] = None

    # Temporal (Requirements 13, 14, 15)
    temporal: Optional[TemporalInfo] = None

    # Source provenance (Requirements 21, 22)
    source_id: Optional[str] = None
    source_version: Optional[int] = None
    source_type: Optional[SourceType] = None
    source_location: Optional[SourceLocation] = None

    # Extraction (Requirement 87)
    extraction: Optional[ExtractionMetadata] = None

    # Confidence (Requirements 16, 17)
    confidence: Optional[ConfidenceDimensions] = None

    # Status (Requirement 42)
    status: EvidenceStatus = EvidenceStatus.ACTIVE
    status_reason: Optional[str] = None

    # Validity (Requirement 43)
    valid_from: Optional[datetime] = None
    valid_until: Optional[datetime] = None

    # Processing
    processing_version: Optional[str] = None

    # Quality gate (Requirement 79)
    quality_gate: Optional[QualityGateResult] = None


class EvidenceCreate(EvidenceBase):
    """Schema for creating new evidence."""
    pass


class EvidenceUpdate(BaseModel):
    """Schema for updating evidence (limited fields)."""
    status: Optional[EvidenceStatus] = None
    status_reason: Optional[str] = None
    valid_until: Optional[datetime] = None
    confidence: Optional[ConfidenceDimensions] = None
    normalization: Optional[NormalizationInfo] = None


class EvidenceRelationshipCreate(BaseModel):
    """Create evidence relationship."""
    source_evidence_id: str
    target_evidence_id: str
    relationship_type: EvidenceRelationshipType
    temporal_relationship: Optional[str] = None
    detection_method: Optional[str] = None
    detection_confidence: Optional[float] = Field(None, ge=0.0, le=1.0)


class EvidenceRelationshipResponse(BaseModel):
    """Evidence relationship with details."""
    id: str
    source_evidence_id: str
    target_evidence_id: str
    relationship_type: EvidenceRelationshipType
    temporal_relationship: Optional[str] = None
    detection_method: Optional[str] = None
    detection_confidence: Optional[float] = None
    is_active: bool
    created_at: datetime
    created_by: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class EvidenceCorrectionCreate(BaseModel):
    """Create evidence correction."""
    evidence_id: str
    correction_type: CorrectionType
    field_name: str
    old_value: Optional[str] = None
    new_value: Optional[str] = None
    correction_reason: Optional[str] = None
    is_source_correction: bool = False
    source_correction_details: Optional[str] = None


class EvidenceCorrectionResponse(BaseModel):
    """Evidence correction with propagation info."""
    id: str
    evidence_id: str
    correction_type: CorrectionType
    field_name: str
    old_value: Optional[str]
    new_value: Optional[str]
    correction_reason: Optional[str]
    is_source_correction: bool
    affected_claims: List[str] = []
    affected_events: List[str] = []
    affected_care_states: List[str] = []
    affected_situations: List[str] = []
    affected_watches: List[str] = []
    affected_summaries: List[str] = []
    applied: bool
    applied_at: Optional[datetime]
    created_at: datetime
    corrected_by: str

    model_config = ConfigDict(from_attributes=True)


class EvidenceInvalidationCreate(BaseModel):
    """Create evidence invalidation."""
    evidence_id: str
    reason: InvalidationReason
    reason_details: Optional[str] = None


class EvidenceInvalidationResponse(BaseModel):
    """Evidence invalidation with propagation tracking."""
    id: str
    evidence_id: str
    reason: InvalidationReason
    reason_details: Optional[str]
    propagation_status: str
    affected_claims: List[str] = []
    affected_events: List[str] = []
    affected_care_states: List[str] = []
    affected_situations: List[str] = []
    affected_watches: List[str] = []
    affected_summaries: List[str] = []
    affected_next_actions: List[str] = []
    propagation_log: List[Dict[str, Any]] = []
    created_at: datetime
    completed_at: Optional[datetime]
    invalidated_by: str

    model_config = ConfigDict(from_attributes=True)


class EvidenceSupersessionCreate(BaseModel):
    """Create evidence supersession."""
    superseded_evidence_id: str
    superseding_evidence_id: str
    reason: SupersessionReason
    reason_details: Optional[str] = None
    effective_from: datetime
    is_explicit: bool = True


class EvidenceSupersessionResponse(BaseModel):
    """Evidence supersession."""
    id: str
    superseded_evidence_id: str
    superseding_evidence_id: str
    reason: SupersessionReason
    reason_details: Optional[str]
    effective_from: datetime
    is_explicit: bool
    established_by: Optional[str]
    established_by_system: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class EvidenceDeduplicationCreate(BaseModel):
    """Propose evidence deduplication."""
    evidence_id_a: str
    evidence_id_b: str
    similarity_score: Optional[float] = Field(None, ge=0.0, le=1.0)
    match_type: str
    matched_fields: Optional[List[str]] = None


class EvidenceDeduplicationResponse(BaseModel):
    """Evidence deduplication record."""
    id: str
    evidence_id_a: str
    evidence_id_b: str
    similarity_score: Optional[float]
    match_type: str
    matched_fields: List[str] = []
    status: str
    confirmed_by: Optional[str]
    created_at: datetime
    confirmed_at: Optional[datetime]

    model_config = ConfigDict(from_attributes=True)


class EvidenceVersionResponse(BaseModel):
    """Evidence version history."""
    id: str
    evidence_id: str
    version: int
    change_type: str
    change_reason: Optional[str]
    changed_by: Optional[str]
    changed_by_system: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class EvidenceResponse(EvidenceBase):
    """Full evidence response with all fields."""
    id: str
    version: int
    created_at: datetime
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class EvidenceQueryParams(BaseModel):
    """Structured evidence query parameters. Requirement 64."""
    person_id: Optional[str] = None
    subject_id: Optional[str] = None
    subject_role: Optional[EvidenceSubjectRole] = None
    type: Optional[EvidenceType] = None
    subtype: Optional[str] = None
    status: Optional[EvidenceStatus] = None
    source_id: Optional[str] = None
    source_type: Optional[SourceType] = None
    observer_role: Optional[EvidenceObserverRole] = None
    observer_id: Optional[str] = None
    author_role: Optional[EvidenceAuthorRole] = None
    author_id: Optional[str] = None
    uploaded_by_caregiver_id: Optional[str] = None
    event_time_from: Optional[datetime] = None
    event_time_to: Optional[datetime] = None
    observation_time_from: Optional[datetime] = None
    observation_time_to: Optional[datetime] = None
    created_at_from: Optional[datetime] = None
    created_at_to: Optional[datetime] = None
    confidence_extraction_min: Optional[float] = Field(None, ge=0.0, le=1.0)
    confidence_entity_min: Optional[float] = Field(None, ge=0.0, le=1.0)
    confidence_temporal_min: Optional[float] = Field(None, ge=0.0, le=1.0)
    has_relationships: Optional[bool] = None
    relationship_type: Optional[EvidenceRelationshipType] = None
    related_to_evidence_id: Optional[str] = None
    negation_status: Optional[NegationStatus] = None
    uncertainty_level: Optional[UncertaintyLevel] = None
    quality_gate_passed: Optional[bool] = None
    limit: int = Field(100, ge=1, le=1000)
    offset: int = Field(0, ge=0)
    sort_by: str = "created_at"
    sort_order: Literal["asc", "desc"] = "desc"


class EvidenceTraceResponse(BaseModel):
    """Full traceability chain. Requirements 22, 23, 24."""
    evidence: EvidenceResponse
    source_document: Optional[Dict[str, Any]] = None
    source_location: Optional[SourceLocation] = None
    supporting_claims: List[Dict[str, Any]] = []
    contradicting_claims: List[Dict[str, Any]] = []
    derived_events: List[Dict[str, Any]] = []
    care_state_components: List[Dict[str, Any]] = []
    situations: List[Dict[str, Any]] = []
    downstream_actions: List[Dict[str, Any]] = []
    corrections: List[EvidenceCorrectionResponse] = []
    invalidations: List[EvidenceInvalidationResponse] = []
    supersessions: List[EvidenceSupersessionResponse] = []
    versions: List[EvidenceVersionResponse] = []
    relationships: List[EvidenceRelationshipResponse] = []


class EvidenceGraphResponse(BaseModel):
    """Evidence graph for a person/entity. Requirement 100."""
    nodes: List[EvidenceResponse]
    edges: List[EvidenceRelationshipResponse]
    clusters: List[Dict[str, Any]] = []  # Grouped by event/claim/situation