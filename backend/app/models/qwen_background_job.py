import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, Index
from sqlalchemy.sql import func

from app.core.database import Base


class QwenBackgroundJob(Base):
    __tablename__ = "qwen_background_jobs"
    __table_args__ = (
        Index("ix_qwen_background_jobs_claim", "status", "available_at", "created_at"),
    )

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id", ondelete="CASCADE"), nullable=False, index=True)
    caregiver_id = Column(String, ForeignKey("caregivers.id", ondelete="CASCADE"), nullable=False, index=True)
    status = Column(String(24), nullable=False, default="queued", server_default="queued", index=True)
    task_text = Column(Text, nullable=False)
    context_text = Column(Text, nullable=False, default="", server_default="")
    evidence_ids_json = Column(Text, nullable=False, default="[]", server_default="[]")
    appointment_id = Column(String(128), nullable=True)
    workflow = Column(String(48), nullable=True)
    result_text = Column(Text, nullable=True)
    source_event_ids_json = Column(Text, nullable=False, default="[]", server_default="[]")
    source_evidence_ids_json = Column(Text, nullable=False, default="[]", server_default="[]")
    attempt_count = Column(Integer, nullable=False, default=0, server_default="0")
    error_code = Column(String(48), nullable=True)
    available_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
    locked_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
