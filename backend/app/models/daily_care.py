import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.sql import func

from app.core.database import Base


class DailyCareEntry(Base):
    __tablename__ = "daily_care_entries"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=False, index=True)

    mood = Column(String, nullable=True)
    mood_scale = Column(Integer, nullable=True)
    agitation_level = Column(String, nullable=True)
    agitation_scale = Column(Integer, nullable=True)
    vitals = Column(JSON, nullable=False, default=dict)
    behavior_notes = Column(Text, nullable=True)
    sleep_notes = Column(Text, nullable=True)
    eating_notes = Column(Text, nullable=True)
    wellbeing_stress = Column(Integer, nullable=True)
    wellbeing_notes = Column(Text, nullable=True)
    general_notes = Column(Text, nullable=True)
    location = Column(JSON, nullable=True)
    location_provenance = Column(String, nullable=True)

    medications = Column(JSON, nullable=False, default=list)
    scales = Column(JSON, nullable=False, default=list)
    notes = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
