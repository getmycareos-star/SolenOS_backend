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


def __getattr__(name):
    """Resolve legacy model names after the model split.

    Older tests and integrations imported core models from this module. Keep
    those imports working while maintaining one canonical mapped class per
    table and avoiding eager circular imports.
    """
    from importlib import import_module

    aliases = {
        "Person": ("app.models.identity", "Person"),
        "Caregiver": ("app.models.caregiver", "Caregiver"),
        "CaregiverModel": ("app.models.caregiver", "Caregiver"),
        "CareEvent": ("app.models.care_event", "CareEvent"),
        "CareEventModel": ("app.models.care_event", "CareEvent"),
        "Evidence": ("app.models.evidence", "Evidence"),
        "EvidenceModel": ("app.models.evidence", "Evidence"),
        "Appointment": ("app.models.time_location", "Appointment"),
        "AppointmentModel": ("app.models.time_location", "Appointment"),
        "CarePlan": ("app.models.care_planning", "CarePlan"),
    }
    target = aliases.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = import_module(target[0])
    return getattr(module, target[1])
