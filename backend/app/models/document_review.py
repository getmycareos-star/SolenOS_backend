"""Append-only caregiver review decisions for extracted document candidates."""
from __future__ import annotations
import uuid
from sqlalchemy import Column, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, Index, event
from sqlalchemy.orm import Session
from sqlalchemy.sql import func
from app.core.database import Base

class DocumentCandidateReview(Base):
    __tablename__ = "document_candidate_reviews"
    __table_args__ = (
        UniqueConstraint("analysis_id", "candidate_index", "decision_version", name="uq_document_candidate_review_version"),
        UniqueConstraint("person_id", "idempotency_key", name="uq_document_candidate_review_idempotency"),
        Index("ix_document_candidate_reviews_person_analysis", "person_id", "analysis_id"),
        Index("ix_document_candidate_reviews_analysis_candidate", "analysis_id", "candidate_index", "decision_version"),
        UniqueConstraint("resulting_assertion_id", name="uq_document_candidate_review_assertion"),
    )
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    analysis_id = Column(String, ForeignKey("capture_analyses.id", ondelete="CASCADE"), nullable=False)
    person_id = Column(String, ForeignKey("persons.id", ondelete="CASCADE"), nullable=False)
    caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=False)
    source_evidence_id = Column(String, ForeignKey("evidence.id", ondelete="CASCADE"), nullable=False)
    candidate_index = Column(Integer, nullable=False)
    decision_version = Column(Integer, nullable=False)
    decision = Column(String(24), nullable=False)
    original_candidate = Column(JSON, nullable=False)
    source_quote = Column(Text, nullable=False)
    corrected_subject = Column(String(160), nullable=True)
    corrected_observation = Column(Text, nullable=True)
    rationale = Column(Text, nullable=True)
    resulting_assertion_id = Column(String, ForeignKey("care_assertions.id"), nullable=True)
    idempotency_key = Column(String(128), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

@event.listens_for(Session, "before_flush")
def _document_candidate_reviews_are_append_only(session, flush_context, instances):
    cls = DocumentCandidateReview
    if any(isinstance(row, cls) for row in session.dirty):
        raise ValueError("Document candidate review history is append-only")
    if any(isinstance(row, cls) for row in session.deleted):
        raise ValueError("Document candidate review history is append-only")
