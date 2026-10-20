"""Canonical Care Graph models - the foundational relationship intelligence layer for SolenOS.

Implements the Care Graph Engine requirements:
- First-class, provenance-backed, versioned relationship model over SolenOS entities
- Canonical nodes representing all care-relevant entities
- Typed edges with full provenance, confidence decomposition, temporal validity
- Immutable version history and evolution log
- Person-scoped with strict boundary enforcement
"""

from sqlalchemy import Column, String, DateTime, Text, Float, Integer, ForeignKey, Boolean, Enum as SQLEnum, Index, UniqueConstraint, JSON
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
import uuid
from app.core.database import Base
from app.core.types import JSONList, JSONDict
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


class CareGraphNode(Base):
    """Canonical node representing a care-relevant entity. Section 7.

    Every node has stable identity. Identity is NEVER display name.
    Uses canonical entity IDs from source systems (Person, CareEvent, Evidence, etc.)
    """

    __tablename__ = "care_graph_nodes"

    # Identity
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    version = Column(Integer, nullable=False, default=1)

    # Person boundary - ABSOLUTE (Requirement 49, Invariant 12)
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)

    # Node classification
    node_type = Column(SQLEnum(CareGraphNodeType), nullable=False, index=True)

    # Canonical entity reference (the "real" object this node represents)
    # Examples: person_id, care_event_id, evidence_id, medication_id, etc.
    canonical_entity_id = Column(String, nullable=False, index=True)
    canonical_entity_type = Column(String, nullable=False)  # e.g., "Person", "CareEvent", "Evidence", "Medication"

    # Display label (NOT identity - for UI only)
    label = Column(String, nullable=True)

    # Identity resolution (Section 9, Invariant 1)
    entity_resolution_status = Column(SQLEnum(CareGraphEntityResolutionStatus), nullable=False, default=CareGraphEntityResolutionStatus.RESOLVED)
    entity_resolution_confidence = Column(Float, nullable=True)
    unresolved_identity_reason = Column(Text, nullable=True)  # Why identity is unresolved

    # Temporal validity of this node's existence in the graph (Section 38)
    valid_from = Column(DateTime(timezone=True), nullable=True)
    valid_to = Column(DateTime(timezone=True), nullable=True)
    temporal_status = Column(SQLEnum(CareGraphTemporalStatus), nullable=False, default=CareGraphTemporalStatus.CURRENT)

    # Provenance - why this node exists in the graph
    provenance_source = Column(SQLEnum(CareGraphProvenanceSource), nullable=False)
    provenance_details = Column(JSONDict, nullable=True)  # {source_evidence_ids, extraction_method, etc.}

    # Quality/validation
    quality_gate_passed = Column(Boolean, nullable=False, default=False)
    quality_gate_details = Column(JSONDict, nullable=True)

    # Processing metadata
    processing_version = Column(String, nullable=True)
    processing_timestamp = Column(DateTime(timezone=True), server_default=func.now())

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    created_by = Column(String, ForeignKey("accounts.id"), nullable=True)  # Account who caused this node
    created_by_system = Column(Boolean, nullable=False, default=False)

    # Relationships
    person = relationship("Person", foreign_keys=[person_id], back_populates="care_graph_nodes")
    creator = relationship("Account", foreign_keys=[created_by], backref="care_graph_nodes_created")
    outgoing_edges = relationship("CareGraphEdge", foreign_keys="CareGraphEdge.subject_node_id", back_populates="subject_node")
    incoming_edges = relationship("CareGraphEdge", foreign_keys="CareGraphEdge.object_node_id", back_populates="object_node")
    change_log_entries = relationship("CareGraphChangeLog", foreign_keys="CareGraphChangeLog.node_id", back_populates="node")

    __table_args__ = (
        # Unique canonical entity per person
        UniqueConstraint("person_id", "canonical_entity_type", "canonical_entity_id", name="uq_care_graph_node_canonical"),
        Index("ix_care_graph_node_person_type", "person_id", "node_type"),
        Index("ix_care_graph_node_canonical", "canonical_entity_type", "canonical_entity_id"),
        Index("ix_care_graph_node_temporal", "valid_from", "valid_to"),
    )


class CareGraphEdge(Base):
    """Canonical typed relationship between nodes. Section 8.

    Every meaningful edge has:
    - Typed predicate from controlled ontology
    - Full provenance chain to evidence
    - Decomposed confidence (not single scalar)
    - Temporal validity bounds
    - Lifecycle status
    - Version history
    """

    __tablename__ = "care_graph_edges"

    # Identity
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    version = Column(Integer, nullable=False, default=1)

    # Person boundary - ABSOLUTE
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)

    # Subject and Object (Section 8)
    subject_node_id = Column(String, ForeignKey("care_graph_nodes.id"), nullable=False, index=True)
    subject_node_type = Column(SQLEnum(CareGraphNodeType), nullable=False)
    object_node_id = Column(String, ForeignKey("care_graph_nodes.id"), nullable=False, index=True)
    object_node_type = Column(SQLEnum(CareGraphNodeType), nullable=False)

    # Relationship type (controlled ontology - Sections 11-24)
    relationship_type = Column(SQLEnum(CareGraphRelationshipType), nullable=False, index=True)
    relationship_subtype = Column(SQLEnum(CareGraphRelationshipSubtype), nullable=True)

    # Directionality
    directionality = Column(SQLEnum(CareGraphDirectionality), nullable=False, default=CareGraphDirectionality.DIRECTED)

    # Temporal validity (Section 21, 38)
    valid_from = Column(DateTime(timezone=True), nullable=True)
    valid_to = Column(DateTime(timezone=True), nullable=True)
    temporal_status = Column(SQLEnum(CareGraphTemporalStatus), nullable=False, default=CareGraphTemporalStatus.CURRENT)
    temporal_precision = Column(String, nullable=True)  # exact, approximate, range, relative

    # Lifecycle (Section 31)
    status = Column(SQLEnum(CareGraphRelationshipStatus), nullable=False, default=CareGraphRelationshipStatus.CANDIDATE, index=True)

    # Decomposed confidence (Section 26) - NOT a single scalar
    confidence_existence = Column(Float, nullable=True)       # Does this relationship exist?
    confidence_subject = Column(Float, nullable=True)         # Subject identity certainty
    confidence_object = Column(Float, nullable=True)          # Object identity certainty
    confidence_type = Column(Float, nullable=True)            # Relationship type certainty
    confidence_temporal = Column(Float, nullable=True)        # Temporal bounds certainty
    confidence_identity = Column(Float, nullable=True)        # Entity resolution certainty
    confidence_source_support = Column(Float, nullable=True)  # How strongly evidence supports this
    confidence_causal = Column(Float, nullable=True)          # Causal claim certainty (if applicable)
    confidence_overall = Column(Float, nullable=True)         # Composite (computed)

    # Provenance chain (Section 28) - minimum conceptual chain:
    # RELATIONSHIP -> DERIVATION -> EVIDENCE -> SOURCE SEGMENT -> RAW SOURCE
    provenance_source = Column(SQLEnum(CareGraphProvenanceSource), nullable=False)
    inference_method = Column(SQLEnum(CareGraphInferenceMethod), nullable=True)
    reasoning_version = Column(String, nullable=True)  # Pipeline/prompt/model version

    # Evidence links (Section 22, 28)
    supporting_evidence_ids = Column(JSONList, nullable=True)      # Evidence IDs supporting this edge
    contradicting_evidence_ids = Column(JSONList, nullable=True)   # Evidence IDs contradicting this edge
    source_relationship_ids = Column(JSONList, nullable=True)      # Source relationship IDs (e.g., CareEventRelationship.id, EvidenceRelationship.id)

    # Causal-specific fields (Section 24, 25)
    causal_basis = Column(String, nullable=True)                  # explicit_source_assertion, temporal_association, etc.
    alternative_explanations = Column(JSONList, nullable=True)    # Alternative causal hypotheses
    confounders = Column(JSONList, nullable=True)                 # Known confounding factors

    # Relationship metadata
    strength = Column(Float, nullable=True)                       # 0-1 relationship strength
    edge_metadata = Column(JSONDict, nullable=True)               # Extensible domain-specific data

    # Source attribution
    created_by = Column(String, ForeignKey("accounts.id"), nullable=True)
    created_by_system = Column(Boolean, nullable=False, default=False)
    created_by_type = Column(String, nullable=True)  # "caregiver", "system", "llm_candidate"

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    person = relationship("Person", foreign_keys=[person_id], back_populates="care_graph_edges")
    subject_node = relationship("CareGraphNode", foreign_keys=[subject_node_id], back_populates="outgoing_edges")
    object_node = relationship("CareGraphNode", foreign_keys=[object_node_id], back_populates="incoming_edges")
    creator = relationship("Account", foreign_keys=[created_by], backref="care_graph_edges_created")
    change_log_entries = relationship("CareGraphChangeLog", foreign_keys="CareGraphChangeLog.edge_id", back_populates="edge")

    __table_args__ = (
        # Prevent duplicate edges of same type between same nodes (Section 34)
        UniqueConstraint("subject_node_id", "object_node_id", "relationship_type", "person_id", name="uq_care_graph_edge_unique"),
        Index("ix_care_graph_edge_person_type", "person_id", "relationship_type"),
        Index("ix_care_graph_edge_subject", "subject_node_id", "relationship_type"),
        Index("ix_care_graph_edge_object", "object_node_id", "relationship_type"),
        Index("ix_care_graph_edge_status", "status"),
        Index("ix_care_graph_edge_temporal", "valid_from", "valid_to"),
    )


class CareGraphEdgeVersion(Base):
    """Immutable history of edge changes. Section 32, Invariant 10.

    Every meaningful relationship version preserved. Never destroyed.
    """

    __tablename__ = "care_graph_edge_versions"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    edge_id = Column(String, ForeignKey("care_graph_edges.id"), nullable=False, index=True)
    volume = Column(Integer, nullable=False)

    # Snapshot of all significant fields
    person_id = Column(String, nullable=False)
    subject_node_id = Column(String, nullable=False)
    subject_node_type = Column(SQLEnum(CareGraphNodeType), nullable=False)
    object_node_id = Column(String, nullable=False)
    object_node_type = Column(SQLEnum(CareGraphNodeType), nullable=False)

    relationship_type = Column(SQLEnum(CareGraphRelationshipType), nullable=False)
    relationship_subtype = Column(SQLEnum(CareGraphRelationshipSubtype), nullable=True)
    directionality = Column(SQLEnum(CareGraphDirectionality), nullable=False)

    valid_from = Column(DateTime(timezone=True), nullable=True)
    valid_to = Column(DateTime(timezone=True), nullable=True)
    temporal_status = Column(SQLEnum(CareGraphTemporalStatus), nullable=False)
    temporal_precision = Column(String, nullable=True)

    status = Column(SQLEnum(CareGraphRelationshipStatus), nullable=False)

    confidence_existence = Column(Float, nullable=True)
    confidence_subject = Column(Float, nullable=True)
    confidence_object = Column(Float, nullable=True)
    confidence_type = Column(Float, nullable=True)
    confidence_temporal = Column(Float, nullable=True)
    confidence_identity = Column(Float, nullable=True)
    confidence_source_support = Column(Float, nullable=True)
    confidence_causal = Column(Float, nullable=True)
    confidence_overall = Column(Float, nullable=True)

    provenance_source = Column(SQLEnum(CareGraphProvenanceSource), nullable=False)
    inference_method = Column(SQLEnum(CareGraphInferenceMethod), nullable=True)
    reasoning_version = Column(String, nullable=True)

    supporting_evidence_ids = Column(JSONList, nullable=True)
    contradicting_evidence_ids = Column(JSONList, nullable=True)
    source_relationship_ids = Column(JSONList, nullable=True)

    causal_basis = Column(String, nullable=True)
    alternative_explanations = Column(JSONList, nullable=True)
    confounders = Column(JSONList, nullable=True)

    strength = Column(Float, nullable=True)
    edge_metadata = Column(JSONDict, nullable=True)

    created_by = Column(String, nullable=True)
    created_by_system = Column(Boolean, nullable=False, default=False)
    created_by_type = Column(String, nullable=True)

    # Change metadata
    change_type = Column(SQLEnum(CareGraphChangeType), nullable=False)
    change_reason = Column(Text, nullable=True)
    changed_by = Column(String, ForeignKey("accounts.id"), nullable=True)
    changed_by_system = Column(Boolean, nullable=False, default=False)
    triggering_evidence_ids = Column(JSONList, nullable=True)  # Evidence that triggered this change
    affected_downstream = Column(JSONList, nullable=True)      # Track what downstream objects may be stale (Section 67)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("edge_id", "volume", name="uq_care_graph_edge_version"),
        Index("ix_care_graph_edge_version_edge", "edge_id", "volume"),
    )


class CareGraphNodeVersion(Base):
    """Immutable history of node changes."""

    __tablename__ = "care_graph_node_versions"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    node_id = Column(String, ForeignKey("care_graph_nodes.id"), nullable=False, index=True)
    version = Column(Integer, nullable=False)

    person_id = Column(String, nullable=False)
    node_type = Column(SQLEnum(CareGraphNodeType), nullable=False)
    canonical_entity_id = Column(String, nullable=False)
    canonical_entity_type = Column(String, nullable=False)
    label = Column(String, nullable=True)

    entity_resolution_status = Column(SQLEnum(CareGraphEntityResolutionStatus), nullable=False)
    entity_resolution_confidence = Column(Float, nullable=True)
    unresolved_identity_reason = Column(Text, nullable=True)

    valid_from = Column(DateTime(timezone=True), nullable=True)
    valid_to = Column(DateTime(timezone=True), nullable=True)
    temporal_status = Column(SQLEnum(CareGraphTemporalStatus), nullable=False)

    provenance_source = Column(SQLEnum(CareGraphProvenanceSource), nullable=False)
    provenance_details = Column(JSONDict, nullable=True)

    change_type = Column(SQLEnum(CareGraphChangeType), nullable=False)
    change_reason = Column(Text, nullable=True)
    changed_by = Column(String, ForeignKey("accounts.id"), nullable=True)
    changed_by_system = Column(Boolean, nullable=False, default=False)
    triggering_evidence_ids = Column(JSONList, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("node_id", "version", name="uq_care_graph_node_version"),
        Index("ix_care_graph_node_version_node", "node_id", "version"),
    )


class CareGraphChangeLog(Base):
    """Auditable evolution stream. Section 51.

    Conceptually: GRAPH_CHANGE with change_id, relationship_id, change_type,
    old_version, new_version, triggering_evidence, actor, timestamp, reason, reasoning_version
    """

    __tablename__ = "care_graph_change_log"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))

    # What changed
    node_id = Column(String, ForeignKey("care_graph_nodes.id"), nullable=True, index=True)
    edge_id = Column(String, ForeignKey("care_graph_edges.id"), nullable=True, index=True)

    change_type = Column(SQLEnum(CareGraphChangeType), nullable=False, index=True)

    # Version tracking
    old_version = Column(Integer, nullable=True)
    new_version = Column(Integer, nullable=True)

    # Provenance of the change
    triggering_evidence_ids = Column(JSONList, nullable=True)  # Evidence that caused this change
    triggering_event_id = Column(String, nullable=True)        # CareEvent that triggered this
    actor_id = Column(String, ForeignKey("accounts.id"), nullable=True)
    actor_type = Column(String, nullable=True)                 # "caregiver", "system", "pipeline"
    reasoning_version = Column(String, nullable=True)          # Pipeline version

    # Human-readable reason
    reason = Column(Text, nullable=True)

    # Downstream impact tracking (Section 67)
    affected_edges = Column(JSONList, nullable=True)
    affected_nodes = Column(JSONList, nullable=True)
    affected_situations = Column(JSONList, nullable=True)
    affected_states = Column(JSONList, nullable=True)
    affected_watches = Column(JSONList, nullable=True)

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    actor = relationship("Account", foreign_keys=[actor_id], backref="care_graph_change_log_actor")
    node = relationship("CareGraphNode", foreign_keys=[node_id], back_populates="change_log_entries")
    edge = relationship("CareGraphEdge", foreign_keys=[edge_id], back_populates="change_log_entries")

    __table_args__ = (
        Index("ix_care_graph_change_log_edge", "edge_id"),
        Index("ix_care_graph_change_log_node", "node_id"),
        Index("ix_care_graph_change_log_type", "change_type"),
        Index("ix_care_graph_change_log_time", "created_at"),
    )


class CareGraphEdgeCorrection(Base):
    """Relationship correction with full history. Section 33.

    CORRECTION: previous representation was wrong
    INVALIDATION: relationship should no longer be considered valid
    SUPERSESSION: newer representation replaces older one
    """

    __tablename__ = "care_graph_edge_corrections"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    edge_id = Column(String, ForeignKey("care_graph_edges.id"), nullable=False, index=True)

    correction_type = Column(SQLEnum(CareGraphCorrectionType), nullable=False)

    # What was corrected
    field_name = Column(String, nullable=False)
    old_value = Column(Text, nullable=True)
    new_value = Column(Text, nullable=True)

    # Provenance
    corrected_by = Column(String, ForeignKey("accounts.id"), nullable=False)
    correction_reason = Column(Text, nullable=True)
    correction_evidence_ids = Column(JSONList, nullable=True)

    # Source vs evidence correction distinction
    is_source_correction = Column(Boolean, nullable=False, default=False)
    source_correction_details = Column(Text, nullable=True)

    # Downstream impact (Section 67)
    affected_edges = Column(JSONList, nullable=True)
    affected_nodes = Column(JSONList, nullable=True)
    affected_situations = Column(JSONList, nullable=True)
    affected_states = Column(JSONList, nullable=True)
    affected_watches = Column(JSONList, nullable=True)
    affected_summaries = Column(JSONList, nullable=True)

    # Status
    applied = Column(Boolean, nullable=False, default=False)
    applied_at = Column(DateTime(timezone=True), nullable=True)
    application_details = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    corrected_by_rel = relationship("Account", foreign_keys=[corrected_by], backref="care_graph_edge_corrections")

    __table_args__ = (
        Index("ix_care_graph_edge_correction_edge", "edge_id"),
        Index("ix_care_graph_edge_correction_applied", "applied"),
    )


class CareGraphEdgeInvalidation(Base):
    """Relationship invalidation with propagation tracking. Section 30, 67."""

    __tablename__ = "care_graph_edge_invalidations"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    edge_id = Column(String, ForeignKey("care_graph_edges.id"), nullable=False, index=True)

    reason = Column(Text, nullable=False)
    reason_details = Column(Text, nullable=True)

    invalidated_by = Column(String, ForeignKey("accounts.id"), nullable=False)

    # Downstream propagation
    propagation_status = Column(String, nullable=False, default="pending")  # pending, in_progress, completed, failed
    affected_edges = Column(JSONList, nullable=True)
    affected_nodes = Column(JSONList, nullable=True)
    affected_situations = Column(JSONList, nullable=True)
    affected_states = Column(JSONList, nullable=True)
    affected_watches = Column(JSONList, nullable=True)
    affected_summaries = Column(JSONList, nullable=True)
    affected_next_actions = Column(JSONList, nullable=True)

    propagation_log = Column(JSONList, nullable=True)  # [{step, target_type, target_id, status, timestamp, details}]

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    invalidated_by_rel = relationship("Account", foreign_keys=[invalidated_by], backref="care_graph_edge_invalidations")

    __table_args__ = (
        Index("ix_care_graph_edge_invalidation_edge", "edge_id"),
        Index("ix_care_graph_edge_invalidation_propagation", "propagation_status"),
    )


class CareGraphEdgeSupersession(Base):
    """Relationship supersession with historical preservation. Section 33.

    SUPERSEDED: valid at one time but no longer current
    INVALIDATED: unreliable/erroneous
    """

    __tablename__ = "care_graph_edge_supersessions"

    id = Column(String, primary_key=lambda: str(uuid.uuid4()))
    superseded_edge_id = Column(String, ForeignKey("care_graph_edges.id"), nullable=False, index=True)
    superseding_edge_id = Column(String, ForeignKey("care_graph_edges.id"), nullable=False, index=True)

    reason = Column(Text, nullable=False)
    reason_details = Column(Text, nullable=True)

    established_by = Column(String, ForeignKey("accounts.id"), nullable=True)
    established_by_system = Column(Boolean, nullable=False, default=False)

    effective_from = Column(DateTime(timezone=True), nullable=False)
    is_explicit = Column(Boolean, nullable=False, default=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    established_by_rel = relationship("Account", foreign_keys=[established_by], backref="care_graph_edge_supersessions")

    __table_args__ = (
        UniqueConstraint("superseded_edge_id", "superseding_edge_id", name="uq_care_graph_edge_supersession"),
        Index("ix_care_graph_edge_supersession_superseding", "superseding_edge_id"),
    )


class CareGraphContradiction(Base):
    """Explicit graph-level contradiction representation. Section 37.

    Example: Document A says medication stopped. Document B says currently taking.
    Do not silently choose one. Represent both with contradiction edge.
    """

    __tablename__ = "care_graph_contradictions"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)

    # The conflicting edges
    edge_a_id = Column(String, ForeignKey("care_graph_edges.id"), nullable=False, index=True)
    edge_b_id = Column(String, ForeignKey("care_graph_edges.id"), nullable=False, index=True)

    # Contradiction characterization
    contradiction_type = Column(String, nullable=False)  # "direct_negation", "temporal_conflict", "status_conflict", "identity_conflict"
    description = Column(Text, nullable=False)

    # Evidence on each side
    edge_a_evidence_ids = Column(JSONList, nullable=True)
    edge_b_evidence_ids = Column(JSONList, nullable=True)

    # Resolution status
    resolution_status = Column(String, nullable=False, default="unresolved")  # unresolved, edge_a_preferred, edge_b_preferred, both_historical, merged
    resolved_by = Column(String, ForeignKey("accounts.id"), nullable=True)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    resolution_reason = Column(Text, nullable=True)

    # Detection
    detected_by = Column(String, nullable=True)  # "system", "caregiver", "pipeline"
    detection_method = Column(String, nullable=True)
    detection_confidence = Column(Float, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    person = relationship("Person", foreign_keys=[person_id])
    edge_a = relationship("CareGraphEdge", foreign_keys=[edge_a_id])
    edge_b = relationship("CareGraphEdge", foreign_keys=[edge_b_id])
    resolver = relationship("Account", foreign_keys=[resolved_by], backref="care_graph_contradictions_resolved")

    __table_args__ = (
        Index("ix_care_graph_contradiction_person", "person_id"),
        Index("ix_care_graph_contradiction_status", "resolution_status"),
    )


# ============================================================
# Graph Grouping Structures (Section 36)
# ============================================================

class CareGraphGroup(Base):
    """Higher-order graph structures. Section 36.

    care_episode, hospitalization_episode, treatment_course, care_pathway,
    symptom_cluster, recurrence_group, provider_relationship, caregiver_network, follow_up_chain
    """

    __tablename__ = "care_graph_groups"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)

    group_type = Column(String, nullable=False, index=True)  # episode, treatment_course, symptom_cluster, etc.
    title = Column(String, nullable=False)
    description = Column(Text, nullable=True)

    # Temporal bounds
    temporal_start = Column(DateTime(timezone=True), nullable=True)
    temporal_end = Column(DateTime(timezone=True), nullable=True)

    # Member nodes and edges
    member_node_ids = Column(JSONList, nullable=True)
    member_edge_ids = Column(JSONList, nullable=True)
    member_count = Column(Integer, nullable=False, default=0)

    # Confidence
    grouping_confidence = Column(Float, nullable=True)
    grouping_reason = Column(Text, nullable=True)

    # Status
    is_active = Column(Boolean, nullable=False, default=True)
    status = Column(String, nullable=False, default="active")  # active, resolved, monitoring

    # Provenance
    created_by = Column(String, ForeignKey("accounts.id"), nullable=True)
    created_by_system = Column(Boolean, nullable=False, default=False)
    detection_method = Column(String, nullable=True)
    detection_evidence_ids = Column(JSONList, nullable=True)

    # Representative node/edge for summary views
    representative_node_id = Column(String, ForeignKey("care_graph_nodes.id"), nullable=True)
    representative_edge_id = Column(String, ForeignKey("care_graph_edges.id"), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    person = relationship("Person", foreign_keys=[person_id])
    creator = relationship("Account", foreign_keys=[created_by], backref="care_graph_groups_created")
    representative_node = relationship("CareGraphNode", foreign_keys=[representative_node_id])
    representative_edge = relationship("CareGraphEdge", foreign_keys=[representative_edge_id])

    __table_args__ = (
        Index("ix_care_graph_group_person_type", "person_id", "group_type"),
        Index("ix_care_graph_group_active", "is_active"),
    )


# Back-references to add to existing models:
# In app/models/care.py Person class:
# care_graph_nodes = relationship("CareGraphNode", foreign_keys="CareGraphNode.person_id", back_populates="person")
# care_graph_edges = relationship("CareGraphEdge", foreign_keys="CareGraphEdge.person_id", back_populates="person")

# In app/models/caregiver.py Caregiver class (if exists):
# care_graph_nodes_created = relationship("CareGraphNode", foreign_keys="CareGraphNode.created_by")
# care_graph_edges_created = relationship("CareGraphEdge", foreign_keys="CareGraphEdge.created_by")
# care_graph_corrections = relationship("CareGraphEdgeCorrection", foreign_keys="CareGraphEdgeCorrection.corrected_by")
# care_graph_invalidations = relationship("CareGraphEdgeInvalidation", foreign_keys="CareGraphEdgeInvalidation.invalidated_by")
