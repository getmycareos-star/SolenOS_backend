"""Consent-gated examples awaiting review for SolenOS product improvement."""
import uuid
from sqlalchemy import Column, DateTime, ForeignKey, String, Text
from sqlalchemy.sql import func
from app.core.database import Base

class ImprovementTrainingExample(Base):
    __tablename__ = "improvement_training_examples"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id", ondelete="CASCADE"), nullable=False, index=True)
    caregiver_id = Column(String, ForeignKey("caregivers.id", ondelete="CASCADE"), nullable=False, index=True)
    category = Column(String(80), nullable=False)
    sample_text = Column(Text, nullable=False)
    status = Column(String(24), nullable=False, default="pending_review", server_default="pending_review", index=True)
    consent_version = Column(String(64), nullable=False)
    consented_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    reviewed_at = Column(DateTime(timezone=True), nullable=True)
