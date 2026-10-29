from pydantic import BaseModel, Field
from datetime import datetime, date
from typing import Optional, List, Any, Literal


class PersonBase(BaseModel):
    name: str
    date_of_birth: Optional[date] = None


class PersonCreate(PersonBase):
    pass


class Person(PersonBase):
    id: str
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class CaregiverBase(BaseModel):
    name: str
    email: Optional[str] = None
    relationship: str
    person_id: str
    timezone: str = "UTC"


class CaregiverCreate(CaregiverBase):
    pass


class Caregiver(CaregiverBase):
    id: str
    created_at: datetime

    class Config:
        from_attributes = True


class EvidenceBase(BaseModel):
    person_id: str
    type: str
    # Independent axes; omitted values remain unclassified rather than inferred.
    evidence_kind: Optional[Literal[
        "direct_observation", "caregiver_report", "professional_observation",
        "person_self_report", "documented_record", "instrument_measurement",
        "photo_video", "audio_transcript", "imported_third_party",
        "historical_recollection", "derived_observation", "inference", "hypothesis",
    ]] = None
    epistemic_status: Optional[Literal[
        "unverified", "uncertain", "unknown", "missing", "disputed",
        "superseded", "withdrawn",
    ]] = None
    source_text: str
    original_file_path: Optional[str] = None
    extra_metadata: Optional[dict[str, Any]] = None
    time_provenance: Optional[str] = None
    location: Optional[str] = None
    confidence: Optional[float] = Field(None, ge=0.0, le=1.0)
    evidence_status: Optional[str] = None
    source_type: Optional[str] = None
    source_span_start: Optional[int] = None
    source_span_end: Optional[int] = None
    # These fields identify the author/reporter/collector independently from uploader.
    original_author: Optional[str] = None
    reported_by_label: Optional[str] = None
    reported_by_caregiver_id: Optional[str] = None
    collected_by_caregiver_id: Optional[str] = None
    collected_at: Optional[datetime] = None
    occurred_at: Optional[datetime] = None
    occurred_at_timezone: Optional[str] = None
    origin_channel: Optional[str] = None
    original_source_reference: Optional[str] = None
    parent_evidence_id: Optional[str] = None
    reported_at: Optional[datetime] = None
    collection_method: Optional[str] = Field(None, max_length=80)
    # Opaque caller-defined label only; do not send IMEI, serial number, or advertising ID.
    origin_device_ref: Optional[str] = Field(None, max_length=160)
    transfer_source: Optional[str] = Field(None, max_length=160)
    transfer_reference: Optional[str] = Field(None, max_length=2000)


class EvidenceCreate(EvidenceBase):
    uploaded_by_caregiver_id: str


class Evidence(EvidenceBase):
    id: str
    uploaded_at: datetime
    uploaded_by_caregiver_id: str
    source_text_sha256: Optional[str] = None
    ingested_at: datetime

    class Config:
        from_attributes = True


class CareEventBase(BaseModel):
    person_id: str
    event_type: str
    status: str = "recorded"
    occurred_at: datetime
    occurred_at_timezone: Optional[str] = None
    title: str
    description: Optional[str] = None
    evidence_ids: Optional[List[str]] = None
    location: Optional[str] = None
    location_provenance: Optional[str] = None
    time_provenance: Optional[str] = None
    tags: Optional[List[str]] = None
    created_by_caregiver_id: str
    significance_verdict: Optional[str] = None
    attention_candidate: Optional[bool] = False
    follow_up_candidate: Optional[bool] = False
    confidence: Optional[float] = Field(None, ge=0.0, le=1.0)
    situation_id: Optional[str] = None


class CareEventCreate(CareEventBase):
    pass


class CareEvent(CareEventBase):
    id: str
    recorded_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class InsightBase(BaseModel):
    person_id: str
    title: str
    description: str
    insight_type: str
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_ids: List[str]
    possible_context: Optional[str] = None
    time_provenance: Optional[str] = None


class InsightCreate(InsightBase):
    pass


class Insight(InsightBase):
    id: str
    created_at: datetime
    updated_at: Optional[datetime] = None
    superseded_by_insight_id: Optional[str] = None
    is_active: bool = True

    class Config:
        from_attributes = True


class CorrectionBase(BaseModel):
    person_id: str
    target_type: str
    target_id: str
    original_text: str
    corrected_text: str
    reason: Optional[str] = None
    caregiver_id: str


class CorrectionCreate(CorrectionBase):
    pass


class Correction(CorrectionBase):
    id: str
    created_at: datetime

    class Config:
        from_attributes = True
