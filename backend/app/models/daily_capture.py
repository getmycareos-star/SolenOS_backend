"""Models for the structured daily forms.

Everything here writes through to Evidence and CareEvent rather than sitting in
a parallel store. That is deliberate: the change detector, the meaning engine,
the contradiction checker and the safety rules all already read those two tables,
so a caregiver tapping five buttons instead of uploading a document lands in
exactly the same pipeline as every other observation, with no new integration to
maintain and no second definition of "what the record says".
"""

from sqlalchemy import Column, String, DateTime, Text, Boolean, Float, ForeignKey, Integer, Enum as SQLEnum
from sqlalchemy.sql import func
import uuid
from app.core.database import Base
from app.core.daily_capture_enums import (
    MedicationStatus,
    ScaleAxis,
    ScaleDirection,
    VitalType,
)


class MedicationRecord(Base):
    """One dose, and whether it actually happened.

    Adherence is the single most-asked clinical question about home care, and it
    is unanswerable from narrative notes. It is answerable here because the
    status is a controlled value and every row carries a timestamp.
    """

    __tablename__ = "medication_records"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    medication_name = Column(String, nullable=False)
    dose = Column(String, nullable=True)
    scheduled_for = Column(DateTime(timezone=True), nullable=True)
    status = Column(SQLEnum(MedicationStatus), nullable=False, index=True)
    administered_at = Column(DateTime(timezone=True), nullable=True)

    # The photo of the bottle is evidence in its own right, linked by id rather
    # than embedded, so the correction cascade can invalidate it like any other.
    evidence_id = Column(String, ForeignKey("evidence.id"), nullable=True)
    care_event_id = Column(String, ForeignKey("care_events.id"), nullable=True)

    # What the caregiver actually said, kept verbatim. Trust rule 3: the system
    # never replaces the caregiver's words with its own.
    note = Column(Text, nullable=True)
    recorded_by_caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=False)
    recorded_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class VitalSign(Base):
    """A manually entered vital sign.

    Entered by hand on purpose. A device integration would produce readings at a
    density the record cannot interpret, and would introduce a second trust
    boundary. A number a caregiver typed, with their name on it, is a number
    somebody actually looked at.
    """

    __tablename__ = "vital_signs"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    vital_type = Column(SQLEnum(VitalType), nullable=False, index=True)
    value = Column(Float, nullable=False)
    unit = Column(String, nullable=True)
    measured_at = Column(DateTime(timezone=True), nullable=False, index=True)

    # Outside the expected range, and flagged for a human. Never an interpretation.
    is_out_of_range = Column(Boolean, nullable=False, default=False)
    range_note = Column(Text, nullable=True)

    evidence_id = Column(String, ForeignKey("evidence.id"), nullable=True)
    care_event_id = Column(String, ForeignKey("care_events.id"), nullable=True)
    note = Column(Text, nullable=True)
    recorded_by_caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=False)
    recorded_at = Column(DateTime(timezone=True), server_default=func.now())


class ScaleObservation(Base):
    """One value on a five-point axis, e.g. agitation = 4."""

    __tablename__ = "scale_observations"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    axis = Column(SQLEnum(ScaleAxis), nullable=False, index=True)
    score = Column(Integer, nullable=False)
    direction = Column(SQLEnum(ScaleDirection), nullable=False)

    # The pole labels shown when the value was entered, kept because "4" only
    # means something next to what 4 was called at the time.
    low_label = Column(String, nullable=True)
    high_label = Column(String, nullable=True)

    observed_at = Column(DateTime(timezone=True), nullable=False, index=True)
    evidence_id = Column(String, ForeignKey("evidence.id"), nullable=True)
    care_event_id = Column(String, ForeignKey("care_events.id"), nullable=True)
    note = Column(Text, nullable=True)
    recorded_by_caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=False)
    recorded_at = Column(DateTime(timezone=True), server_default=func.now())
