from datetime import datetime, timezone
from typing import Literal, List, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


CoverageStatus = Literal["covered", "partial", "uncovered", "unknown"]


class ObservationCoverageCreate(BaseModel):
    person_id: str = Field(min_length=1, max_length=128)
    subject_type: str = Field(min_length=1, max_length=120)
    window_start: datetime
    window_end: datetime
    coverage_status: CoverageStatus
    source_evidence_ids: List[str] = Field(default_factory=list, max_length=100)
    rationale: str = Field(min_length=3, max_length=2000)

    @model_validator(mode="after")
    def validate_window(self):
        if not self.subject_type.strip():
            raise ValueError("subject_type must not be blank")
        if not self.rationale.strip():
            raise ValueError("rationale must not be blank")
        for name, value in (("window_start", self.window_start), ("window_end", self.window_end)):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{name} must include an explicit timezone offset")
        if self.window_end <= self.window_start:
            raise ValueError("window_end must be later than window_start")
        return self


class ObservationCoverageRecord(BaseModel):
    # Request validation requires explicit timezone offsets. Read models accept
    # database-normalized timestamps; SQLite strips timezone metadata from
    # timezone-aware columns, so normalize those values without rerunning the
    # request-only validator.
    id: str
    person_id: str
    subject_type: str
    window_start: datetime
    window_end: datetime
    coverage_status: CoverageStatus
    source_evidence_ids: List[str]
    rationale: str
    recorded_by_caregiver_id: str
    created_at: datetime

    @field_validator("window_start", "window_end", "created_at", mode="before")
    @classmethod
    def normalize_sqlite_naive_datetimes(cls, value):
        if isinstance(value, datetime) and (value.tzinfo is None or value.utcoffset() is None):
            return value.replace(tzinfo=timezone.utc)
        return value

    model_config = ConfigDict(from_attributes=True)


class CoverageAssessment(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    person_id: str
    subject_type: str
    window_start: datetime
    window_end: datetime
    coverage_status: CoverageStatus
    covered_seconds: int
    window_seconds: int
    coverage_fraction: Optional[float]
    records: List[ObservationCoverageRecord]
    limitations: List[str]
