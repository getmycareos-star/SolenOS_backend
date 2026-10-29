from datetime import datetime
from typing import Literal, List, Optional
from pydantic import BaseModel, ConfigDict, Field, model_validator


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
        if self.window_end <= self.window_start:
            raise ValueError("window_end must be later than window_start")
        return self


class ObservationCoverageRecord(ObservationCoverageCreate):
    id: str
    recorded_by_caregiver_id: str
    created_at: datetime

    class Config:
        from_attributes = True


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
