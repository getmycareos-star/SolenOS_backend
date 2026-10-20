from sqlalchemy import Column, String, DateTime, Date, Boolean, Text, ForeignKey, Float, Integer
from sqlalchemy.sql import func
import uuid
from app.core.database import Base
from app.core.types import JSONList
from sqlalchemy.orm import relationship

# CareEvent class has been moved to app/models/care_event.py
# This file now contains only Insight and Correction classes


class Insight(Base):
    __tablename__ = "insights"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    title = Column(String, nullable=False)
    description = Column(Text, nullable=False)
    insight_type = Column(String, nullable=False)
    confidence = Column(Float, nullable=False)
    evidence_ids = Column(JSONList, nullable=False)
    possible_context = Column(Text, nullable=True)
    time_provenance = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    superseded_by_insight_id = Column(String, nullable=True)
    is_active = Column(Boolean, default=True)


class Correction(Base):
    __tablename__ = "corrections"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    target_type = Column(String, nullable=False)
    target_id = Column(String, nullable=False)
    original_text = Column(Text, nullable=False)
    corrected_text = Column(Text, nullable=False)
    reason = Column(Text, nullable=True)
    caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
