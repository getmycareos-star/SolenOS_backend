"""Request/response contracts for daily capture and evidence upload."""
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator


class MedicationCapture(BaseModel):
    medication_name: str = Field(min_length=1, max_length=300)
    status: str = Field(min_length=1, max_length=80)
    dose: Optional[str] = Field(default=None, max_length=200)
    scheduled_for: Optional[datetime] = None
    administered_at: Optional[datetime] = None
    photo_evidence_id: Optional[str] = Field(default=None, max_length=100)
    note: Optional[str] = Field(default=None, max_length=4000)

    @field_validator("medication_name", "status")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("This field cannot be blank")
        return value


class VitalCapture(BaseModel):
    vital_type: Literal["bp_systolic", "bp_diastolic", "heart_rate", "oxygen_sat", "temperature", "weight"]
    value: float
    measured_at: datetime
    unit: Optional[str] = Field(default=None, max_length=40)
    note: Optional[str] = Field(default=None, max_length=4000)


class ScaleCapture(BaseModel):
    axis: Literal["mood", "agitation", "wellbeing_stress"]
    score: float = Field(ge=0, le=10)
    observed_at: datetime
    note: Optional[str] = Field(default=None, max_length=4000)


class DailyCaptureInput(BaseModel):
    person_id: str = Field(min_length=1, max_length=100)
    caregiver_id: str = Field(min_length=1, max_length=100)
    medications: list[MedicationCapture] = Field(default_factory=list, max_length=100)
    vitals: list[VitalCapture] = Field(default_factory=list, max_length=100)
    scales: list[ScaleCapture] = Field(default_factory=list, max_length=100)
    notes: str = Field(default="", max_length=20000)


class DailyCaptureOutput(BaseModel):
    success: bool
    daily_capture_id: str
    care_event_id: str
    recorded_at: datetime


class UploadOutput(BaseModel):
    evidence_id: str
    content_type: str
    size_bytes: int
    text_extracted: bool = False
    message: str


