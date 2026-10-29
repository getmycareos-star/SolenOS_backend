"""Auditable observation coverage for longitudinal care comparisons."""
import uuid
from sqlalchemy import Column, DateTime, ForeignKey, String, Text
from sqlalchemy.sql import func
from app.core.database import Base
from app.core.types import JSONList


class ObservationCoverage(Base):
    __tablename__ = "observation_coverage"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    subject_type = Column(String(120), nullable=False, index=True)
    window_start = Column(DateTime(timezone=True), nullable=False, index=True)
    window_end = Column(DateTime(timezone=True), nullable=False, index=True)
    coverage_status = Column(String(16), nullable=False, index=True)
    source_evidence_ids = Column(JSONList, nullable=False, default=list)
    rationale = Column(Text, nullable=False)
    recorded_by_caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
