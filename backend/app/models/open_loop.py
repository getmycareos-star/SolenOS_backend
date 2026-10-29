"""Persistent open-loop ORM records, kept separate from API routing.

Importing the database layer must not import an API module that itself imports
application models. Keeping these table definitions here prevents a circular
import during migration validation and local SQLite metadata bootstrap.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, Index, Integer, JSON, String, Text,
    UniqueConstraint,
)
from app.core.database import Base


class OpenLoopRecord(Base):
    __tablename__ = "open_loop_records"
    __table_args__ = (
        Index("ix_open_loop_scope_status", "person_id", "caregiver_id", "status"),
        Index("ix_open_loop_scope_deadline", "person_id", "caregiver_id", "deadline_at"),
        Index("ix_open_loop_source_fingerprint", "person_id", "source_fingerprint"),
    )
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=False, index=True)
    loop_type = Column(String(40), nullable=False)
    title = Column(String(240), nullable=False)
    description = Column(Text, nullable=False)
    status = Column(String(40), nullable=False, default="open")
    outcome_status = Column(String(32), nullable=False, default="outcome_unknown")
    owner_role = Column(String(100), nullable=True)
    owner_label = Column(String(200), nullable=True)
    source_type = Column(String(80), nullable=True)
    source_id = Column(String(160), nullable=True)
    source_fingerprint = Column(String(64), nullable=False)
    evidence = Column(JSON, nullable=False, default=list)
    uncertainty = Column(String(40), nullable=False, default="unknown")
    missing_information = Column(JSON, nullable=False, default=list)
    options = Column(JSON, nullable=False, default=list)
    request_text = Column(Text, nullable=True)
    recipient_label = Column(String(200), nullable=True)
    requested_at = Column(DateTime(timezone=True), nullable=True)
    last_contact_at = Column(DateTime(timezone=True), nullable=True)
    response_evidence = Column(JSON, nullable=False, default=list)
    action_evidence = Column(JSON, nullable=False, default=list)
    outcome_evidence = Column(JSON, nullable=False, default=list)
    verification_rule = Column(Text, nullable=True)
    deadline_at = Column(DateTime(timezone=True), nullable=True)
    deadline_source = Column(String(40), nullable=True)
    deadline_timezone = Column(String(80), nullable=True)
    consequence_of_delay = Column(String(40), nullable=True)
    priority = Column(String(16), nullable=False, default="normal")
    priority_reasons = Column(JSON, nullable=False, default=list)
    deferred_reason = Column(Text, nullable=True)
    review_at = Column(DateTime(timezone=True), nullable=True)
    inactive_since = Column(DateTime(timezone=True), nullable=True)
    closure_evidence = Column(JSON, nullable=False, default=list)
    closure_result = Column(String(40), nullable=True)
    closure_actor = Column(String(160), nullable=True)
    closed_at = Column(DateTime(timezone=True), nullable=True)
    cancellation_reason = Column(Text, nullable=True)
    recurrence_of_id = Column(String, ForeignKey("open_loop_records.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    version = Column(Integer, nullable=False, default=1)


class OpenLoopEvent(Base):
    __tablename__ = "open_loop_events"
    __table_args__ = (
        Index("ix_open_loop_events_record_created", "loop_id", "created_at"),
        UniqueConstraint("person_id", "idempotency_key", name="uq_open_loop_event_idempotency"),
    )
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    loop_id = Column(String, ForeignKey("open_loop_records.id", ondelete="CASCADE"), nullable=False, index=True)
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=False, index=True)
    event_type = Column(String(60), nullable=False)
    before_state = Column(JSON, nullable=True)
    after_state = Column(JSON, nullable=False, default=dict)
    evidence = Column(JSON, nullable=False, default=list)
    idempotency_key = Column(String(160), nullable=True)
    actor = Column(String(160), nullable=False, default="session_caregiver")
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))


class OpenLoopOccurrence(Base):
    __tablename__ = "open_loop_occurrences"
    __table_args__ = (
        UniqueConstraint("person_id", "idempotency_key", name="uq_open_loop_occurrence_idempotency"),
        UniqueConstraint("person_id", "source_type", "source_id", "loop_id", name="uq_open_loop_occurrence_source"),
        Index("ix_open_loop_occurrence_record_created", "loop_id", "created_at"),
    )
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    loop_id = Column(String, ForeignKey("open_loop_records.id", ondelete="CASCADE"), nullable=False, index=True)
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=False, index=True)
    source_type = Column(String(80), nullable=True)
    source_id = Column(String(160), nullable=True)
    source_fingerprint = Column(String(64), nullable=False)
    evidence = Column(JSON, nullable=False, default=list)
    idempotency_key = Column(String(160), nullable=True)
    occurred_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))


class OpenLoopDependency(Base):
    __tablename__ = "open_loop_dependencies"
    __table_args__ = (
        UniqueConstraint("prerequisite_id", "dependent_id", name="uq_open_loop_dependency_pair"),
        Index("ix_open_loop_dependency_dependent", "dependent_id"),
    )
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=False, index=True)
    prerequisite_id = Column(String, ForeignKey("open_loop_records.id", ondelete="CASCADE"), nullable=False)
    dependent_id = Column(String, ForeignKey("open_loop_records.id", ondelete="CASCADE"), nullable=False)
    mandatory = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    removed_at = Column(DateTime(timezone=True), nullable=True)
