from pydantic import BaseModel, Field, field_validator
from datetime import datetime
from typing import Optional, List


def _require_aware_datetime(value):
    """Reject naive API timestamps instead of silently treating local time as UTC."""
    if value is not None and (value.tzinfo is None or value.utcoffset() is None):
        raise ValueError("Provide a timezone offset for every date and time.")
    return value

from app.core.temporal_enums import (
    TemporalPrecision,
    TemporalMode,
    TemporalStatus,
    TemporalRelationType,
    TemporalResolutionStatus,
)


class TemporalFactBase(BaseModel):
    @field_validator("asserted_point", "asserted_start", "asserted_end", "lower_bound", "upper_bound", "document_time", "effective_time")
    @classmethod
    def validate_temporal_timezone(cls, value):
        return _require_aware_datetime(value)

    person_id: str
    subject_type: str
    subject_id: str
    temporal_mode: TemporalMode = TemporalMode.EVENT
    asserted_point: Optional[datetime] = None
    asserted_start: Optional[datetime] = None
    asserted_end: Optional[datetime] = None
    precision: TemporalPrecision = TemporalPrecision.UNKNOWN
    is_approximate: bool = False
    lower_bound: Optional[datetime] = None
    upper_bound: Optional[datetime] = None
    document_time: Optional[datetime] = None
    effective_time: Optional[datetime] = None
    time_provenance: Optional[str] = None
    source_assertion: Optional[str] = None
    evidence_ids: Optional[List[str]] = None
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    created_by_caregiver_id: Optional[str] = None


class TemporalFactCreate(TemporalFactBase):
    pass


class TemporalFactUpdate(BaseModel):
    @field_validator("asserted_point", "asserted_start", "asserted_end", "lower_bound", "upper_bound", "document_time", "effective_time")
    @classmethod
    def validate_temporal_timezone(cls, value):
        return _require_aware_datetime(value)

    temporal_mode: Optional[TemporalMode] = None
    asserted_point: Optional[datetime] = None
    asserted_start: Optional[datetime] = None
    asserted_end: Optional[datetime] = None
    precision: Optional[TemporalPrecision] = None
    is_approximate: Optional[bool] = None
    lower_bound: Optional[datetime] = None
    upper_bound: Optional[datetime] = None
    document_time: Optional[datetime] = None
    effective_time: Optional[datetime] = None
    time_provenance: Optional[str] = None
    source_assertion: Optional[str] = None
    evidence_ids: Optional[List[str]] = None
    confidence: Optional[float] = Field(None, ge=0.0, le=1.0)


class TemporalFact(TemporalFactBase):
    id: str
    status: TemporalStatus = TemporalStatus.ASSERTED
    superseded_by_fact_id: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class TemporalReferenceFrameBase(BaseModel):
    @field_validator("reference_time")
    @classmethod
    def validate_reference_timezone(cls, value):
        return _require_aware_datetime(value)

    person_id: str
    name: str
    reference_time: Optional[datetime] = None
    source_event_id: Optional[str] = None
    source_fact_id: Optional[str] = None
    is_resolved: bool = False


class TemporalReferenceFrameCreate(TemporalReferenceFrameBase):
    pass


class TemporalReferenceFrame(TemporalReferenceFrameBase):
    id: str
    created_at: datetime

    class Config:
        from_attributes = True


class TemporalRelationBase(BaseModel):
    person_id: str
    fact_a_id: str
    fact_b_id: str
    relation_type: TemporalRelationType
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    is_derived: bool = False
    derivation_method: Optional[str] = None
    evidence_ids: Optional[List[str]] = None


class TemporalRelationCreate(TemporalRelationBase):
    pass


class TemporalRelation(TemporalRelationBase):
    id: str
    created_at: datetime

    class Config:
        from_attributes = True


class TemporalContradictionBase(BaseModel):
    person_id: str
    subject_type: str
    subject_id: str
    fact_a_id: str
    fact_b_id: str
    description: Optional[str] = None
    resolution_notes: Optional[str] = None
    resolved_by_caregiver_id: Optional[str] = None
    evidence_ids: Optional[List[str]] = None


class TemporalContradictionCreate(TemporalContradictionBase):
    pass


class TemporalContradictionUpdate(BaseModel):
    resolution_status: Optional[TemporalResolutionStatus] = None
    resolution_notes: Optional[str] = None
    resolved_by_caregiver_id: Optional[str] = None
    evidence_ids: Optional[List[str]] = None


class TemporalContradiction(TemporalContradictionBase):
    id: str
    resolution_status: TemporalResolutionStatus = TemporalResolutionStatus.UNRESOLVED
    resolved_at: Optional[datetime] = None
    created_at: datetime

    class Config:
        from_attributes = True


class RelativeTimeResolution(BaseModel):
    original_text: str
    reference_frame_id: Optional[str] = None
    reference_frame_name: Optional[str] = None
    resolved_point: Optional[datetime] = None
    is_resolved: bool
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    derivation_method: Optional[str] = None
    lower_bound: Optional[datetime] = None
    upper_bound: Optional[datetime] = None


class TemporalViewAtPoint(BaseModel):
    person_id: str
    point_in_time: datetime
    active_facts: List[dict]
    historical_facts: List[dict]
    future_facts: List[dict]
    unknown_facts: List[dict]


class SubjectTemporalView(BaseModel):
    person_id: str
    subject_type: str
    subject_id: str
    facts: List[dict]
    relations: List[dict]
    contradictions: List[dict]
    reference_frames: List[dict]
