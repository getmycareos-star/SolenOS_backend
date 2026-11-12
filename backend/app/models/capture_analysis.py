import uuid

from sqlalchemy import Column, DateTime, ForeignKey, JSON, String, Text, Integer, UniqueConstraint, Index, event
from sqlalchemy.orm import Session
from sqlalchemy.sql import func

from app.core.database import Base


class CaptureAnalysis(Base):
    __tablename__ = "capture_analyses"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=False, index=True)
    source_event_id = Column(String, nullable=True, index=True)
    source_evidence_id = Column(String, nullable=True, index=True)
    source_text = Column(Text, nullable=False)
    extraction_status = Column(String(24), nullable=False)
    candidates = Column(JSON, nullable=True)
    review_history = Column(JSON, nullable=False, default=list)
    execution_status = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), nullable=True)



class DocumentReviewEvent(Base):
    """Immutable, append-only caregiver decision for one document candidate version."""

    __tablename__ = "document_review_events"
    __table_args__ = (
        UniqueConstraint(
            "analysis_id", "candidate_index", "decision_version",
            name="uq_document_review_candidate_version",
        ),
        Index(
            "ix_document_review_events_person_analysis",
            "person_id", "analysis_id", "created_at",
        ),
    )

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    analysis_id = Column(
        String, ForeignKey("capture_analyses.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    person_id = Column(String, ForeignKey("persons.id", ondelete="RESTRICT"), nullable=False, index=True)
    caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=False, index=True)
    source_evidence_id = Column(String, ForeignKey("evidence.id", ondelete="RESTRICT"), nullable=False)
    candidate_index = Column(Integer, nullable=False)
    decision_version = Column(Integer, nullable=False)
    decision = Column(String(16), nullable=False)
    rationale = Column(Text, nullable=True)
    before_state = Column(JSON, nullable=False)
    after_state = Column(JSON, nullable=False)
    resulting_assertion_id = Column(String, ForeignKey("care_assertions.id", ondelete="RESTRICT"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


@event.listens_for(Session, "before_flush")
def prevent_document_review_event_mutation(session, flush_context, instances):
    if any(
        isinstance(item, DocumentReviewEvent)
        for item in list(session.dirty) + list(session.deleted)
    ):
        raise ValueError("Document review history is append-only and cannot be updated or deleted")
