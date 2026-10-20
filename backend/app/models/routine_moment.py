"""Routine moment model."""

from sqlalchemy import Column, String, DateTime, ForeignKey, Integer, JSON, Float, func
from sqlalchemy.orm import relationship
import uuid
from app.core.database import Base
from app.models.identity import Person

class RoutineMoment(Base):
    """Routine moment representing a specific observation of a routine."""

    __tablename__ = "routine_moments"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    version = Column(Integer, nullable=False, default=1)
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    evidence_id = Column(String, nullable=True)  # TODO: ForeignKey to evidence.id
    care_event_id = Column(String, nullable=True)  # TODO: ForeignKey to care_events.id
    routine_name = Column(String, nullable=False)
    observation_text = Column(String, nullable=False)
    observation_data = Column(JSON, nullable=True)
    observed_at = Column(DateTime(timezone=True), nullable=False)
    confidence = Column(Float, nullable=True)
    status = Column(String, nullable=False, default="active")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    created_by_caregiver_id = Column(String, nullable=True)  # TODO: ForeignKey to caregiver_profiles.id or accounts.id

    # Relationships
    person = relationship("Person", foreign_keys=[person_id])
    # TODO: Add relationships for evidence_id, care_event_id, created_by_caregiver_id when we know the models.
