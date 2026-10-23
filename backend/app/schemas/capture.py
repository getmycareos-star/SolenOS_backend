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


class LocationCapture(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    accuracy: Optional[float] = Field(default=None, ge=0)
    timestamp: Optional[float] = None


class DailyCaptureInput(BaseModel):
    person_id: str = Field(min_length=1, max_length=100)
    caregiver_id: str = Field(min_length=1, max_length=100)
    medications: list[MedicationCapture] = Field(default_factory=list, max_length=100)
    vitals: list[VitalCapture] = Field(default_factory=list, max_length=100)
    scales: list[ScaleCapture] = Field(default_factory=list, max_length=100)
    notes: str = Field(default="", max_length=20000)
    location: Optional[LocationCapture] = None
    location_provenance: Optional[Literal["user_provided"]] = None


class DailyCaptureOutput(BaseModel):
    success: bool
    daily_capture_id: str
    care_event_id: str
    recorded_at: datetime
    analysis_status: str = "complete"
    analysis_error: Optional[str] = None
    what_changed: Optional[str] = None
    attention_candidate: bool = False
    follow_up_candidate: bool = False
    situations: list[dict] = Field(default_factory=list)
    qwen_extraction_status: str = "not_configured"
    qwen_candidates: Optional[dict] = None
    qwen_analysis_id: Optional[str] = None


class DailyCaptureRecordOutput(BaseModel):
    """A saved capture returned only after care-record read authorization."""
    id: str
    person_id: str
    caregiver_id: str
    recorded_by_account_id: str
    medications: list[dict]
    vitals: list[dict]
    scales: list[dict]
    notes: str
    location: Optional[dict] = None
    location_provenance: Optional[str] = None
    recorded_at: datetime

    model_config = {"from_attributes": True}


class UploadOutput(BaseModel):
    evidence_id: str
    content_type: str
    size_bytes: int
    text_extracted: bool = False
    message: str




class CaptureCandidateReviewInput(BaseModel):
    """Explicit caregiver review of an extracted candidate; never an LCR write."""

    candidate_index: int = Field(ge=0, le=29)
    decision: Literal["accept", "correct", "reject"]
    corrected_subject: Optional[str] = Field(default=None, max_length=160)
    corrected_observation: Optional[str] = Field(default=None, max_length=1200)
    note: Optional[str] = Field(default=None, max_length=1000)
