from sqlalchemy import Column, String, DateTime, Text, Boolean, Float, ForeignKey, Enum as SQLEnum, event
from sqlalchemy.sql import func
from sqlalchemy.orm import Session
import uuid
from app.core.database import Base
from app.core.types import JSONList
from app.core.uncertainty_enums import (
    EpistemicState,
    GapReason,
    Priority,
    GapLifecycleStatus,
    ResolutionMechanism,
)


class InformationGap(Base):
    __tablename__ = "information_gaps"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    subject_type = Column(String, nullable=False)
    subject_id = Column(String, nullable=False)
    field = Column(String, nullable=False)
    epistemic_state = Column(SQLEnum(EpistemicState), nullable=False, default=EpistemicState.UNKNOWN)
    gap_reason = Column(SQLEnum(GapReason), nullable=False, default=GapReason.ABSENT)
    priority = Column(SQLEnum(Priority), nullable=False, default=Priority.INFORMATIONAL)
    lifecycle_status = Column(SQLEnum(GapLifecycleStatus), nullable=False, default=GapLifecycleStatus.OPEN)
    description = Column(Text, nullable=True)
    expected_by = Column(DateTime(timezone=True), nullable=True)
    resolution_mechanism = Column(SQLEnum(ResolutionMechanism), nullable=True)
    resolution_date = Column(DateTime(timezone=True), nullable=True)
    resolution_notes = Column(Text, nullable=True)
    evidence_ids = Column(JSONList, nullable=True)
    context = Column(JSONList, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    created_by_caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=True)
    updated_by_caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=True)


class OpenQuestion(Base):
    __tablename__ = "open_questions"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    question = Column(Text, nullable=False)
    subject_type = Column(String, nullable=False)
    subject_id = Column(String, nullable=False)
    status = Column(String, nullable=False, default="open")
    priority = Column(SQLEnum(Priority), nullable=False, default=Priority.INFORMATIONAL)
    evidence_ids = Column(JSONList, nullable=True)
    context = Column(JSONList, nullable=True)
    asked_at = Column(DateTime(timezone=True), server_default=func.now())
    answered_at = Column(DateTime(timezone=True), nullable=True)
    answer = Column(Text, nullable=True)
    answer_provenance = Column(Text, nullable=True)
    closed_at = Column(DateTime(timezone=True), nullable=True)
    closed_reason = Column(Text, nullable=True)
    created_by_caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=True)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class Contradiction(Base):
    __tablename__ = "contradictions"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    subject_type = Column(String, nullable=False)
    subject_id = Column(String, nullable=False)
    field = Column(String, nullable=False)
    description = Column(Text, nullable=False)
    event_id = Column(String, ForeignKey("care_events.id", ondelete="SET NULL"), nullable=True)
    context_key = Column(String(160), nullable=True)
    effective_from = Column(DateTime(timezone=True), nullable=True)
    effective_to = Column(DateTime(timezone=True), nullable=True)
    matching_candidate_ids = Column(JSONList, nullable=False, default=list)
    evidence_ids = Column(JSONList, nullable=True)
    # Source assertion IDs are part of the persisted conflict provenance.
    # The existing migration defines this column as TEXT; JSONList preserves
    # the same storage representation while providing structured Python values.
    source_assertion_ids = Column(JSONList, nullable=True)
    resolution_status = Column(String, nullable=False, default="unresolved")
    resolution_notes = Column(Text, nullable=True)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    resolved_by_caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class UncertaintyAssessment(Base):
    __tablename__ = "uncertainty_assessments"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    subject_type = Column(String, nullable=False)
    subject_id = Column(String, nullable=False)
    field = Column(String, nullable=False)
    epistemic_state = Column(SQLEnum(EpistemicState), nullable=False)
    confidence = Column(Float, nullable=True)
    known_value = Column(Text, nullable=True)
    gap_reason = Column(SQLEnum(GapReason), nullable=True)
    context = Column(JSONList, nullable=True)
    evidence_ids = Column(JSONList, nullable=True)
    temporal_precision = Column(String, nullable=True)
    assessed_at = Column(DateTime(timezone=True), server_default=func.now())
    assessed_by_caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=True)
    superseded_by_assessment_id = Column(String, nullable=True)
    is_active = Column(Boolean, default=True)



class ContradictionResolutionEvent(Base):
    """Immutable audit record for each conflict resolution/reopening transition."""
    __tablename__ = "contradiction_resolution_events"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    contradiction_id = Column(String, ForeignKey("contradictions.id", ondelete="CASCADE"), nullable=False, index=True)
    person_id = Column(String, ForeignKey("persons.id", ondelete="CASCADE"), nullable=False, index=True)
    actor_caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=True)
    previous_status = Column(String(32), nullable=False)
    new_status = Column(String(32), nullable=False)
    rationale = Column(Text, nullable=False)
    evidence_ids = Column(JSONList, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


@event.listens_for(Session, "before_flush")
def prevent_conflict_history_mutation(session, flush_context, instances):
    if any(
        isinstance(item, ContradictionResolutionEvent)
        for item in list(session.dirty) + list(session.deleted)
    ):
        raise ValueError("Conflict resolution history is append-only and cannot be updated or deleted")
