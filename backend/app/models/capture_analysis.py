"""Persisted, review-only Qwen extraction candidates linked to source care events."""
import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Index, JSON, String, Text, UniqueConstraint
from sqlalchemy.sql import func

from app.core.database import Base


class CaptureAnalysis(Base):
    __tablename__ = "capture_analyses"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    care_event_id = Column(String, ForeignKey("care_events.id", ondelete="CASCADE"), nullable=False)
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    source_evidence_ids = Column(JSON, nullable=False, default=list)
    provider_status = Column(String(32), nullable=False)
    status = Column(String(32), nullable=False, index=True)
    model_name = Column(String(160), nullable=True)
    candidate_data = Column(JSON, nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), nullable=True)

    __table_args__ = (
        UniqueConstraint("care_event_id", name="uq_capture_analysis_care_event"),
        Index("ix_capture_analyses_person_created", "person_id", "created_at"),
    )
