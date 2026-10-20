"""Pydantic schemas for Care Graph API layer.

Request/response models for all Care Graph operations.
"""

from datetime import datetime
from typing import Optional, List, Dict, Any, Union
from pydantic import BaseModel, Field, field_validator
from uuid import UUID

from app.core.graph_enums import (
    CareGraphNodeType,
    CareGraphRelationshipType,
    CareGraphRelationshipSubtype,
    CareGraphRelationshipStatus,
    CareGraphDirectionality,
    CareGraphTemporalStatus,
    CareGraphConfidenceDimension,
    CareGraphChangeType,
    CareGraphCorrectionType,
    CareGraphEntityResolutionStatus,
    CareGraphProvenanceSource,
    CareGraphInferenceMethod,
)


# ============================================================
# Base Schemas
# ============================================================

class CareGraphBase(BaseModel):
    """Base schema with common fields."""
    person_id: str = Field(..., description="Person boundary - absolute requirement")


class CareGraphTimestampMixin(BaseModel):
    """Timestamp fields."""
    created_at: datetime
    updated_at: Optional[datetime] = None


# ============================================================
# Node Schemas
# ============================================================

class CareGraphNodeBase(CareGraphBase):
    """Base node fields."""
    node_type: CareGraphNodeType
    canonical_entity_id: str = Field(..., description="Stable ID from source system")
    canonical_entity_type: str = Field(..., description="Source entity type (Person, CareEvent, Evidence, etc.)")
    label: Optional[str] = Field(None, description="Display label only - NOT identity")
    
    entity_resolution_status: CareGraphEntityResolutionStatus = CareGraphEntityResolutionStatus.RESOLVED
    entity_resolution_confidence: Optional[float] = Field(None, ge=0.0, le=1.0)
    unresolved_identity_reason: Optional[str] = None
    
    valid_from: Optional[datetime] = None
    valid_to: Optional[datetime] = None
    temporal_status: CareGraphTemporalStatus = CareGraphTemporalStatus.CURRENT
    
    provenance_source: CareGraphProvenanceSource
    provenance_details: Optional[Dict[str, Any]] = None
    
    quality_gate_passed: bool = False
    quality_gate_details: Optional[Dict[str, Any]] = None
    
    processing_version: Optional[str] = None
    created_by: Optional[str] = None
    created_by_system: bool = False


class CareGraphNodeCreate(CareGraphNodeBase):
    """Schema for creating a node."""
    pass


class CareGraphNodeUpdate(BaseModel):
    """Schema for updating a node."""
    label: Optional[str] = None
    entity_resolution_status: Optional[CareGraphEntityResolutionStatus] = None
    entity_resolution_confidence: Optional[float] = Field(None, ge=0.0, le=1.0)
    unresolved_identity_reason: Optional[str] = None
    valid_from: Optional[datetime] = None
    valid_to: Optional[datetime] = None
    temporal_status: Optional[CareGraphTemporalStatus] = None
    quality_gate_passed: Optional[bool] = None
    quality_gate_details: Optional[Dict[str, Any]] = None
    provenance_details: Optional[Dict[str, Any]] = None


class CareGraphNodeResponse(CareGraphNodeBase, CareGraphTimestampMixin):
    """Node response with all fields."""
    id: str
    version: int
    processing_timestamp: datetime
    
    class Config:
        from_attributes = True


class CareGraphNodeVersionResponse(BaseModel):
    """Immutable node version snapshot."""
    id: str
    node_id: str
    version: int
    person_id: str
    node_type: CareGraphNodeType
    canonical_entity_id: str
    canonical_entity_type: str
    label: Optional[str] = None
    entity_resolution_status: CareGraphEntityResolutionStatus
    entity_resolution_confidence: Optional[float] = None
    unresolved_identity_reason: Optional[str] = None
    valid_from: Optional[datetime] = None
    valid_to: Optional[datetime] = None
    temporal_status: CareGraphTemporalStatus
    provenance_source: CareGraphProvenanceSource
    provenance_details: Optional[Dict[str, Any]] = None
    change_type: CareGraphChangeType
    change_reason: Optional[str] = None
    changed_by: Optional[str] = None
    changed_by_system: bool = False
    triggering_evidence_ids: Optional[List[str]] = None
    created_at: datetime
    
    class Config:
        from_attributes = True


# ============================================================
# Edge Schemas
# ============================================================

class ConfidenceDimensions(BaseModel):
    """Decomposed confidence - NOT a single scalar. Section 26."""
    existence: Optional[float] = Field(None, ge=0.0, le=1.0, description="Does this relationship exist?")
    subject: Optional[float] = Field(None, ge=0.0, le=1.0, description="Subject identity certainty")
    object: Optional[float] = Field(None, ge=0.0, le=1.0, description="Object identity certainty")
    type: Optional[float] = Field(None, ge=0.0, le=1.0, description="Relationship type certainty")
    temporal: Optional[float] = Field(None, ge=0.0, le=1.0, description="Temporal bounds certainty")
    identity: Optional[float] = Field(None, ge=0.0, le=1.0, description="Entity resolution certainty")
    source_support: Optional[float] = Field(None, ge=0.0, le=1.0, description="How strongly evidence supports this")
    causal: Optional[float] = Field(None, ge=0.0, le=1.0, description="Causal claim certainty (if applicable)")
    overall: Optional[float] = Field(None, ge=0.0, le=1.0, description="Composite (computed)")

    @field_validator('overall', mode='before')
    @classmethod
    def compute_overall(cls, v, info):
        """Compute composite confidence if not provided."""
        if v is not None:
            return v
        dims = [info.data.get(k) for k in ['existence', 'subject', 'object', 'type', 'temporal', 'identity', 'source_support', 'causal']]
        valid = [d for d in dims if d is not None]
        return sum(valid) / len(valid) if valid else None


class CareGraphEdgeBase(CareGraphBase):
    """Base edge fields - typed relationship with full provenance."""
    subject_node_id: str = Field(..., description="Source node ID")
    subject_node_type: CareGraphNodeType
    object_node_id: str = Field(..., description="Target node ID")
    object_node_type: CareGraphNodeType
    
    relationship_type: CareGraphRelationshipType
    relationship_subtype: Optional[CareGraphRelationshipSubtype] = None
    directionality: CareGraphDirectionality = CareGraphDirectionality.DIRECTED
    
    valid_from: Optional[datetime] = None
    valid_to: Optional[datetime] = None
    temporal_status: CareGraphTemporalStatus = CareGraphTemporalStatus.CURRENT
    temporal_precision: Optional[str] = Field(None, description="exact, approximate, range, relative")
    
    status: CareGraphRelationshipStatus = CareGraphRelationshipStatus.CANDIDATE
    
    confidence: Optional[ConfidenceDimensions] = None
    
    provenance_source: CareGraphProvenanceSource
    inference_method: Optional[CareGraphInferenceMethod] = None
    reasoning_version: Optional[str] = None
    
    supporting_evidence_ids: Optional[List[str]] = Field(default_factory=list)
    contradicting_evidence_ids: Optional[List[str]] = Field(default_factory=list)
    source_relationship_ids: Optional[List[str]] = Field(default_factory=list)
    
    causal_basis: Optional[str] = None
    alternative_explanations: Optional[List[str]] = Field(default_factory=list)
    confounders: Optional[List[str]] = Field(default_factory=list)
    
    strength: Optional[float] = Field(None, ge=0.0, le=1.0)
    edge_metadata: Optional[Dict[str, Any]] = None
    
    created_by: Optional[str] = None
    created_by_system: bool = False
    created_by_type: Optional[str] = Field(None, description="caregiver, system, llm_candidate")


class CareGraphEdgeCreate(CareGraphEdgeBase):
    """Schema for creating an edge."""
    pass


class CareGraphEdgeUpdate(BaseModel):
    """Schema for updating an edge - only mutable fields."""
    relationship_subtype: Optional[CareGraphRelationshipSubtype] = None
    directionality: Optional[CareGraphDirectionality] = None
    valid_from: Optional[datetime] = None
    valid_to: Optional[datetime] = None
    temporal_status: Optional[CareGraphTemporalStatus] = None
    temporal_precision: Optional[str] = None
    status: Optional[CareGraphRelationshipStatus] = None
    confidence: Optional[ConfidenceDimensions] = None
    inference_method: Optional[CareGraphInferenceMethod] = None
    reasoning_version: Optional[str] = None
    supporting_evidence_ids: Optional[List[str]] = None
    contradicting_evidence_ids: Optional[List[str]] = None
    source_relationship_ids: Optional[List[str]] = None
    causal_basis: Optional[str] = None
    alternative_explanations: Optional[List[str]] = None
    confounders: Optional[List[str]] = None
    strength: Optional[float] = Field(None, ge=0.0, le=1.0)
    edge_metadata: Optional[Dict[str, Any]] = None


class CareGraphEdgeResponse(CareGraphEdgeBase, CareGraphTimestampMixin):
    """Edge response with all fields."""
    id: str
    version: int
    
    class Config:
        from_attributes = True


class CareGraphEdgeVersionResponse(BaseModel):
    """Immutable edge version snapshot."""
    id: str
    edge_id: str
    version: int
    person_id: str
    subject_node_id: str
    subject_node_type: CareGraphNodeType
    object_node_id: str
    object_node_type: CareGraphNodeType
    relationship_type: CareGraphRelationshipType
    relationship_subtype: Optional[CareGraphRelationshipSubtype] = None
    directionality: CareGraphDirectionality
    valid_from: Optional[datetime] = None
    valid_to: Optional[datetime] = None
    temporal_status: CareGraphTemporalStatus
    temporal_precision: Optional[str] = None
    status: CareGraphRelationshipStatus
    confidence_existence: Optional[float] = None
    confidence_subject: Optional[float] = None
    confidence_object: Optional[float] = None
    confidence_type: Optional[float] = None
    confidence_temporal: Optional[float] = None
    confidence_identity: Optional[float] = None
    confidence_source_support: Optional[float] = None
    confidence_causal: Optional[float] = None
    confidence_overall: Optional[float] = None
    provenance_source: CareGraphProvenanceSource
    inference_method: Optional[CareGraphInferenceMethod] = None
    reasoning_version: Optional[str] = None
    supporting_evidence_ids: Optional[List[str]] = None
    contradicting_evidence_ids: Optional[List[str]] = None
    source_relationship_ids: Optional[List[str]] = None
    causal_basis: Optional[str] = None
    alternative_explanations: Optional[List[str]] = None
    confounders: Optional[List[str]] = None
    strength: Optional[float] = None
    edge_metadata: Optional[Dict[str, Any]] = None
    created_by: Optional[str] = None
    created_by_system: bool = False
    created_by_type: Optional[str] = None
    change_type: CareGraphChangeType
    change_reason: Optional[str] = None
    changed_by: Optional[str] = None
    changed_by_system: bool = False
    triggering_evidence_ids: Optional[List[str]] = None
    affected_downstream: Optional[List[str]] = None
    created_at: datetime
    
    class Config:
        from_attributes = True


# ============================================================
# Change Log Schemas
# ============================================================

class CareGraphChangeLogBase(BaseModel):
    """Base change log entry."""
    change_type: CareGraphChangeType
    old_version: Optional[int] = None
    new_version: Optional[int] = None
    triggering_evidence_ids: Optional[List[str]] = None
    triggering_event_id: Optional[str] = None
    actor_id: Optional[str] = None
    actor_type: Optional[str] = None
    reasoning_version: Optional[str] = None
    reason: Optional[str] = None
    affected_edges: Optional[List[str]] = None
    affected_nodes: Optional[List[str]] = None
    affected_situations: Optional[List[str]] = None
    affected_states: Optional[List[str]] = None
    affected_watches: Optional[List[str]] = None


class CareGraphChangeLogCreate(CareGraphChangeLogBase):
    """Schema for creating a change log entry."""
    node_id: Optional[str] = None
    edge_id: Optional[str] = None


class CareGraphChangeLogResponse(CareGraphChangeLogBase):
    """Change log response."""
    id: str
    node_id: Optional[str] = None
    edge_id: Optional[str] = None
    actor: Optional[Dict[str, Any]] = None
    created_at: datetime
    
    class Config:
        from_attributes = True


# ============================================================
# Correction/Invalidation/Supersession Schemas
# ============================================================

class CareGraphEdgeCorrectionBase(BaseModel):
    """Base correction fields."""
    correction_type: CareGraphCorrectionType
    field_name: str
    old_value: Optional[str] = None
    new_value: Optional[str] = None
    correction_reason: Optional[str] = None
    correction_evidence_ids: Optional[List[str]] = None
    is_source_correction: bool = False
    source_correction_details: Optional[str] = None
    affected_edges: Optional[List[str]] = None
    affected_nodes: Optional[List[str]] = None
    affected_situations: Optional[List[str]] = None
    affected_states: Optional[List[str]] = None
    affected_watches: Optional[List[str]] = None
    affected_summaries: Optional[List[str]] = None


class CareGraphEdgeCorrectionCreate(CareGraphEdgeCorrectionBase):
    """Schema for creating a correction."""
    edge_id: str
    corrected_by: str


class CareGraphEdgeCorrectionResponse(CareGraphEdgeCorrectionBase):
    """Correction response."""
    id: str
    edge_id: str
    corrected_by: str
    applied: bool = False
    applied_at: Optional[datetime] = None
    application_details: Optional[str] = None
    created_at: datetime
    
    class Config:
        from_attributes = True


class CareGraphEdgeInvalidationBase(BaseModel):
    """Base invalidation fields."""
    reason: str
    reason_details: Optional[str] = None
    affected_edges: Optional[List[str]] = None
    affected_nodes: Optional[List[str]] = None
    affected_situations: Optional[List[str]] = None
    affected_states: Optional[List[str]] = None
    affected_watches: Optional[List[str]] = None
    affected_summaries: Optional[List[str]] = None
    affected_next_actions: Optional[List[str]] = None


class CareGraphEdgeInvalidationCreate(CareGraphEdgeInvalidationBase):
    """Schema for creating an invalidation."""
    edge_id: str
    invalidated_by: str


class CareGraphEdgeInvalidationResponse(CareGraphEdgeInvalidationBase):
    """Invalidation response."""
    id: str
    edge_id: str
    invalidated_by: str
    propagation_status: str = "pending"
    propagation_log: Optional[List[Dict[str, Any]]] = None
    created_at: datetime
    completed_at: Optional[datetime] = None
    
    class Config:
        from_attributes = True


class CareGraphEdgeSupersessionBase(BaseModel):
    """Base supersession fields."""
    reason: str
    reason_details: Optional[str] = None
    established_by: Optional[str] = None
    established_by_system: bool = False
    effective_from: datetime
    is_explicit: bool = True


class CareGraphEdgeSupersessionCreate(CareGraphEdgeSupersessionBase):
    """Schema for creating a supersession."""
    superseded_edge_id: str
    superseding_edge_id: str


class CareGraphEdgeSupersessionResponse(CareGraphEdgeSupersessionBase):
    """Supersession response."""
    id: str
    superseded_edge_id: str
    superseding_edge_id: str
    created_at: datetime
    
    class Config:
        from_attributes = True


# ============================================================
# Contradiction Schemas
# ============================================================

class CareGraphContradictionBase(BaseModel):
    """Base contradiction fields."""
    contradiction_type: str = Field(..., description="direct_negation, temporal_conflict, status_conflict, identity_conflict")
    description: str
    edge_a_evidence_ids: Optional[List[str]] = None
    edge_b_evidence_ids: Optional[List[str]] = None
    resolution_status: str = "unresolved"
    resolved_by: Optional[str] = None
    resolved_at: Optional[datetime] = None
    resolution_reason: Optional[str] = None
    detected_by: Optional[str] = None
    detection_method: Optional[str] = None
    detection_confidence: Optional[float] = Field(None, ge=0.0, le=1.0)


class CareGraphContradictionCreate(CareGraphContradictionBase):
    """Schema for creating a contradiction."""
    person_id: str
    edge_a_id: str
    edge_b_id: str


class CareGraphContradictionResponse(CareGraphContradictionBase):
    """Contradiction response."""
    id: str
    person_id: str
    edge_a_id: str
    edge_b_id: str
    created_at: datetime
    
    class Config:
        from_attributes = True


# ============================================================
# Group Schemas
# ============================================================

class CareGraphGroupBase(BaseModel):
    """Base group fields."""
    person_id: str
    group_type: str = Field(..., description="episode, treatment_course, symptom_cluster, etc.")
    title: str
    description: Optional[str] = None
    temporal_start: Optional[datetime] = None
    temporal_end: Optional[datetime] = None
    member_node_ids: Optional[List[str]] = Field(default_factory=list)
    member_edge_ids: Optional[List[str]] = Field(default_factory=list)
    member_count: int = 0
    grouping_confidence: Optional[float] = Field(None, ge=0.0, le=1.0)
    grouping_reason: Optional[str] = None
    is_active: bool = True
    status: str = "active"
    created_by: Optional[str] = None
    created_by_system: bool = False
    detection_method: Optional[str] = None
    detection_evidence_ids: Optional[List[str]] = None
    representative_node_id: Optional[str] = None
    representative_edge_id: Optional[str] = None


class CareGraphGroupCreate(CareGraphGroupBase):
    """Schema for creating a group."""
    pass


class CareGraphGroupUpdate(BaseModel):
    """Schema for updating a group."""
    title: Optional[str] = None
    description: Optional[str] = None
    temporal_start: Optional[datetime] = None
    temporal_end: Optional[datetime] = None
    member_node_ids: Optional[List[str]] = None
    member_edge_ids: Optional[List[str]] = None
    member_count: Optional[int] = None
    grouping_confidence: Optional[float] = Field(None, ge=0.0, le=1.0)
    grouping_reason: Optional[str] = None
    is_active: Optional[bool] = None
    status: Optional[str] = None
    representative_node_id: Optional[str] = None
    representative_edge_id: Optional[str] = None


class CareGraphGroupResponse(CareGraphGroupBase, CareGraphTimestampMixin):
    """Group response."""
    id: str
    creator: Optional[Dict[str, Any]] = None
    representative_node: Optional[Dict[str, Any]] = None
    representative_edge: Optional[Dict[str, Any]] = None
    
    class Config:
        from_attributes = True


# ============================================================
# Query Schemas
# ============================================================

class NeighborQuery(BaseModel):
    """Query for neighbors."""
    node_id: str
    relationship_types: Optional[List[CareGraphRelationshipType]] = None
    direction: str = Field("both", pattern="^(incoming|outgoing|both)$")
    include_inactive: bool = False
    max_depth: int = Field(1, ge=1, le=5)
    temporal_as_of: Optional[datetime] = None
    min_confidence: Optional[float] = Field(None, ge=0.0, le=1.0)


class PathQuery(BaseModel):
    """Query for paths between nodes."""
    source_node_id: str
    target_node_id: str
    max_depth: int = Field(4, ge=1, le=10)
    relationship_types: Optional[List[CareGraphRelationshipType]] = None
    temporal_as_of: Optional[datetime] = None
    require_active: bool = True
    min_confidence: Optional[float] = Field(None, ge=0.0, le=1.0)
    max_paths: int = Field(10, ge=1, le=100)


class SnapshotQuery(BaseModel):
    """Query for graph snapshot at a point in time."""
    person_id: str
    as_of: datetime
    include_inactive: bool = False
    node_types: Optional[List[CareGraphNodeType]] = None
    relationship_types: Optional[List[CareGraphRelationshipType]] = None


class EvidenceLookupQuery(BaseModel):
    """Query for evidence supporting/contradicting edges."""
    edge_ids: Optional[List[str]] = None
    node_ids: Optional[List[str]] = None
    person_id: Optional[str] = None
    evidence_ids: Optional[List[str]] = None
    relationship_types: Optional[List[CareGraphRelationshipType]] = None


# ============================================================
# Response Schemas
# ============================================================

class NeighborResponse(BaseModel):
    """Neighbor query response."""
    node: CareGraphNodeResponse
    edge: CareGraphEdgeResponse
    direction: str  # "incoming" or "outgoing"
    depth: int


class PathResponse(BaseModel):
    """Path query response."""
    nodes: List[CareGraphNodeResponse]
    edges: List[CareGraphEdgeResponse]
    total_confidence: float
    path_length: int


class GraphSnapshotResponse(BaseModel):
    """Graph snapshot at a point in time."""
    person_id: str
    as_of: datetime
    nodes: List[CareGraphNodeResponse]
    edges: List[CareGraphEdgeResponse]
    node_count: int
    edge_count: int


class EvidenceLookupResponse(BaseModel):
    """Evidence lookup response."""
    edge_id: str
    supporting_evidence: List[Dict[str, Any]]
    contradicting_evidence: List[Dict[str, Any]]
    evidence_chain: List[Dict[str, Any]]  # Full provenance chain


# ============================================================
# Evolution Schemas
# ============================================================

class EdgeLifecycleTransition(BaseModel):
    """Request to transition edge lifecycle status."""
    edge_id: str
    new_status: CareGraphRelationshipStatus
    reason: str
    actor_id: str
    actor_type: str = "caregiver"
    triggering_evidence_ids: Optional[List[str]] = None
    reasoning_version: Optional[str] = None


class EdgeCorrectionRequest(BaseModel):
    """Request to apply a correction."""
    edge_id: str
    correction_type: CareGraphCorrectionType
    field_name: str
    new_value: str
    old_value: Optional[str] = None
    correction_reason: str
    corrected_by: str
    correction_evidence_ids: Optional[List[str]] = None
    is_source_correction: bool = False
    source_correction_details: Optional[str] = None


class EdgeInvalidationRequest(BaseModel):
    """Request to invalidate an edge."""
    edge_id: str
    reason: str
    reason_details: Optional[str] = None
    invalidated_by: str


class EdgeSupersessionRequest(BaseModel):
    """Request to create a supersession."""
    superseded_edge_id: str
    superseding_edge_id: str
    reason: str
    reason_details: Optional[str] = None
    established_by: Optional[str] = None
    established_by_system: bool = False
    effective_from: datetime
    is_explicit: bool = True


class ContradictionResolutionRequest(BaseModel):
    """Request to resolve a contradiction."""
    contradiction_id: str
    resolution_status: str = Field(..., pattern="^(edge_a_preferred|edge_b_preferred|both_historical|merged)$")
    resolved_by: str
    resolution_reason: str


# ============================================================
# Bulk Operations
# ============================================================

class BulkNodeCreate(BaseModel):
    """Bulk create nodes."""
    nodes: List[CareGraphNodeCreate]


class BulkEdgeCreate(BaseModel):
    """Bulk create edges."""
    edges: List[CareGraphEdgeCreate]


class BulkOperationResponse(BaseModel):
    """Response for bulk operations."""
    created: int
    failed: int
    errors: List[Dict[str, Any]]
    created_ids: List[str]


# ============================================================
# Stats/Analytics Schemas
# ============================================================

class GraphStatsResponse(BaseModel):
    """Graph statistics."""
    person_id: str
    node_count: int
    edge_count: int
    nodes_by_type: Dict[str, int]
    edges_by_type: Dict[str, int]
    edges_by_status: Dict[str, int]
    corrections_count: int
    invalidations_count: int
    supersessions_count: int
    contradictions_count: int
    groups_count: int
    latest_change: Optional[datetime] = None


class ConfidenceDistributionResponse(BaseModel):
    """Confidence dimension distribution."""
    dimension: CareGraphConfidenceDimension
    buckets: Dict[str, int]  # "0.0-0.1": count, etc.
    mean: float
    median: float
    count: int


# ============================================================
# Enums for API documentation
# ============================================================

class CareGraphEnumsResponse(BaseModel):
    """All enums for API consumers."""
    node_types: List[str]
    relationship_types: List[str]
    relationship_subtypes: List[str]
    relationship_statuses: List[str]
    directionalities: List[str]
    temporal_statuses: List[str]
    confidence_dimensions: List[str]
    change_types: List[str]
    correction_types: List[str]
    entity_resolution_statuses: List[str]
    provenance_sources: List[str]
    inference_methods: List[str]