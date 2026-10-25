import uuid

from sqlalchemy import Column, DateTime, ForeignKey, JSON, String, Text
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
