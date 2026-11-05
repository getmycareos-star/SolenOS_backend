import uuid

from sqlalchemy import Column, DateTime, ForeignKey, JSON, String, Text, event, inspect as sa_inspect
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
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), nullable=True)


@event.listens_for(Session, "before_flush")
def _document_extraction_source_is_immutable(session, flush_context, instances):
    for row in session.dirty:
        if not isinstance(row, CaptureAnalysis):
            continue
        state = sa_inspect(row)
        previous_candidates = state.attrs.candidates.history.deleted
        original_metadata = previous_candidates[0] if previous_candidates else row.candidates
        if not isinstance(original_metadata, dict) or original_metadata.get("kind") != "document_extraction_v1":
            continue
        immutable_fields = (
            "person_id", "caregiver_id", "source_event_id",
            "source_evidence_id", "source_text",
        )
        if any(state.attrs[field].history.has_changes() for field in immutable_fields):
            raise ValueError("Persisted document extraction source and identity are immutable")
