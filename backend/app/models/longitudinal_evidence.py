"""Longitudinal Evidence Tracking Model.

Tracks how evidence evolves over time for a person/entity:
- Periodic snapshots of evidence state
- Confidence trends
- Status changes
- Relationship evolution
- Aggregated statistics
"""

from sqlalchemy import Column, String, DateTime, Text, Float, Integer, ForeignKey, Boolean, Enum as SQLEnum, Index, UniqueConstraint
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
import uuid
from app.core.database import Base
from app.core.types import JSONList, JSONDict
from app.core.evidence_enums import EvidenceType, EvidenceStatus


class LongitudinalEvidenceSnapshot(Base):
    """Immutable snapshot of evidence state at a point in time.

    Created periodically (daily/weekly/monthly) or on significant changes.
    """
    __tablename__ = "longitudinal_evidence_snapshots"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))

    # Scope
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    snapshot_time = Column(DateTime(timezone=True), nullable=False, index=True)
    period_type = Column(String, nullable=False, default="daily")  # daily, weekly, monthly, event_driven
    period_label = Column(String, nullable=True)  # "2026-10-06", "Week 40", etc.

    # Aggregated counts
    total_evidence_count = Column(Integer, nullable=False, default=0)
    active_evidence_count = Column(Integer, nullable=False, default=0)
    invalidated_evidence_count = Column(Integer, nullable=False, default=0)
    superseded_evidence_count = Column(Integer, nullable=False, default=0)
    corrected_evidence_count = Column(Integer, nullable=False, default=0)

    # Breakdown by type
    evidence_by_type = Column(JSONDict, nullable=True)  # { "MEDICATION_TAKEN": 5, "BLOOD_PRESSURE": 12, ... }
    evidence_by_status = Column(JSONDict, nullable=True)  # { "ACTIVE": 15, "INVALIDATED": 2, ... }

    # Confidence trends
    avg_confidence_overall = Column(Float, nullable=True)
    avg_confidence_extraction = Column(Float, nullable=True)
    avg_confidence_entity_resolution = Column(Float, nullable=True)
    avg_confidence_temporal = Column(Float, nullable=True)
    avg_confidence_source_support = Column(Float, nullable=True)
    avg_confidence_interpretation = Column(Float, nullable=True)

    # Relationship metrics
    total_relationships = Column(Integer, nullable=False, default=0)
    relationships_by_type = Column(JSONDict, nullable=True)  # { "CONTRADICTS": 3, "SUPPORTS": 8, ... }

    # Quality metrics
    avg_quality_gate_score = Column(Float, nullable=True)
    quality_gate_pass_rate = Column(Float, nullable=True)

    # Processing metrics
    avg_processing_time_ms = Column(Float, nullable=True)
    extraction_method_distribution = Column(JSONDict, nullable=True)

    # Evidence IDs in this snapshot
    evidence_ids = Column(JSONList, nullable=True)

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("person_id", "snapshot_time", "period_type", name="uq_longitudinal_snapshot"),
        Index("ix_longitudinal_person_time", "person_id", "snapshot_time"),
        Index("ix_longitudinal_period", "period_type", "snapshot_time"),
    )


class LongitudinalEvidenceTrend(Base):
    """Pre-computed trend metrics for evidence over time.

    Computed from snapshots for quick trend analysis.
    """
    __tablename__ = "longitudinal_evidence_trends"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))

    # Scope
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    evidence_type = Column(SQLEnum(EvidenceType), nullable=False, index=True)

    # Time window
    window_start = Column(DateTime(timezone=True), nullable=False, index=True)
    window_end = Column(DateTime(timezone=True), nullable=False, index=True)
    window_type = Column(String, nullable=False, default="weekly")  # daily, weekly, monthly

    # Trend metrics
    count_trend = Column(String, nullable=True)  # increasing, decreasing, stable, volatile
    count_change_pct = Column(Float, nullable=True)
    confidence_trend = Column(String, nullable=True)
    avg_confidence_change = Column(Float, nullable=True)

    # Volatility
    status_change_count = Column(Integer, nullable=False, default=0)
    relationship_change_count = Column(Integer, nullable=False, default=0)
    correction_count = Column(Integer, nullable=False, default=0)
    invalidation_count = Column(Integer, nullable=False, default=0)

    # First/last seen
    first_evidence_id = Column(String, nullable=True)
    last_evidence_id = Column(String, nullable=True)
    first_seen = Column(DateTime(timezone=True), nullable=True)
    last_seen = Column(DateTime(timezone=True), nullable=True)

    # Metadata
    computed_at = Column(DateTime(timezone=True), server_default=func.now())
    data_points = Column(Integer, nullable=False, default=0)

    __table_args__ = (
        UniqueConstraint("person_id", "evidence_type", "window_start", "window_type", name="uq_longitudinal_trend"),
        Index("ix_longitudinal_trend_person_type", "person_id", "evidence_type", "window_start"),
    )


class LongitudinalEvidenceEvent(Base):
    """Tracks significant events in evidence lifecycle.

    Captures discrete events: created, corrected, invalidated, superseded, etc.
    """
    __tablename__ = "longitudinal_evidence_events"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))

    # Scope
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    evidence_id = Column(String, ForeignKey("evidence.id"), nullable=False, index=True)

    # Event details
    event_type = Column(String, nullable=False, index=True)  # created, updated, corrected, invalidated, superseded, deduplicated, relationship_added, relationship_removed
    event_category = Column(String, nullable=False, default="lifecycle")  # lifecycle, quality, relationship, propagation

    # Change summary
    changed_fields = Column(JSONList, nullable=True)  # List of field names that changed
    old_values = Column(JSONDict, nullable=True)  # Old values for changed fields
    new_values = Column(JSONDict, nullable=True)  # New values for changed fields

    # Context
    triggered_by = Column(String, nullable=True)  # caregiver_id, system, pipeline
    trigger_details = Column(Text, nullable=True)

    # Impact
    downstream_impact_count = Column(Integer, nullable=False, default=0)
    affected_entities = Column(JSONList, nullable=True)  # IDs of affected claims, events, states, etc.

    # Timestamps
    event_time = Column(DateTime(timezone=True), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_longitudinal_event_person_time", "person_id", "event_time"),
        Index("ix_longitudinal_event_evidence", "evidence_id", "event_time"),
        Index("ix_longitudinal_event_type", "event_type", "event_time"),
    )
