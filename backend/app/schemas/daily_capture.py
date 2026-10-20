"""Request and response shapes for the structured daily forms."""

from datetime import datetime
from typing import Optional, List

from pydantic import ConfigDict, BaseModel, Field

from app.core.daily_capture_enums import (
    MedicationStatus,
    ScaleAxis,
    ScaleDirection,
    VitalType,
)


class MedicationRecordCreate(BaseModel):
    person_id: str
    medication_name: str
    status: MedicationStatus
    caregiver_id: str
    dose: Optional[str] = None
    scheduled_for: Optional[datetime] = None
    administered_at: Optional[datetime] = None
    # Id of an evidence row already uploaded by the app, e.g. the bottle photo.
    photo_evidence_id: Optional[str] = None
    note: Optional[str] = None


class MedicationRecordResponse(BaseModel):
    id: str
    person_id: str
    medication_name: str
    status: MedicationStatus
    # The model column is recorded_by_caregiver_id; the response exposes it as
    # caregiver_id to match the request shape a client already holds.
    caregiver_id: str = Field(validation_alias="recorded_by_caregiver_id")
    dose: Optional[str] = None
    scheduled_for: Optional[datetime] = None
    administered_at: Optional[datetime] = None
    evidence_id: Optional[str] = None
    care_event_id: Optional[str] = None
    note: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class VitalSignCreate(BaseModel):
    person_id: str
    vital_type: VitalType
    value: float
    measured_at: datetime
    caregiver_id: str
    unit: Optional[str] = None
    note: Optional[str] = None


class VitalSignResponse(BaseModel):
    id: str
    person_id: str
    vital_type: VitalType
    value: float
    unit: Optional[str] = None
    measured_at: datetime
    is_out_of_range: bool
    range_note: Optional[str] = None
    care_event_id: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class ScaleObservationCreate(BaseModel):
    person_id: str
    axis: ScaleAxis
    score: int = Field(..., ge=1, le=5)
    observed_at: datetime
    caregiver_id: str
    note: Optional[str] = None


class ScaleObservationResponse(BaseModel):
    id: str
    person_id: str
    axis: ScaleAxis
    score: int
    direction: ScaleDirection
    low_label: Optional[str] = None
    high_label: Optional[str] = None
    observed_at: datetime
    care_event_id: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class DailyLogCreate(BaseModel):
    """The whole daily form in one request.

    A caregiver filling in medication, vitals and scales taps submit once, and a
    partial save that leaves the record internally inconsistent is worse than no
    save at all. The endpoint below is therefore transactional.
    """

    person_id: str
    caregiver_id: str
    medications: List[MedicationRecordCreate] = Field(default_factory=list)
    vitals: List[VitalSignCreate] = Field(default_factory=list)
    scales: List[ScaleObservationCreate] = Field(default_factory=list)
    notes: Optional[str] = None


class DailyLogResponse(BaseModel):
    person_id: str
    recorded_at: datetime
    medications: List[MedicationRecordResponse]
    vitals: List[VitalSignResponse]
    scales: List[ScaleObservationResponse]
    # The evidence and event ids the form produced, so a caller can point the
    # user at the exact rows that were written.
    evidence_ids: List[str]
    care_event_ids: List[str]


class AdherenceTrendResponse(BaseModel):
    person_id: str
    medication_name: str
    window_days: int
    scheduled_count: int
    given_count: int
    refused_count: int
    missed_count: int
    adherence_rate: Optional[float] = None
    # True when adherence has fallen relative to the earlier half of the window.
    declining: bool = False
    evidence_ids: List[str] = Field(default_factory=list)
    possible_context: Optional[str] = None
    confidence: Optional[float] = None
