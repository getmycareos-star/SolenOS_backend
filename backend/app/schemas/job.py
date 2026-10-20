from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime
from app.models.job import JobStatus, JobType


class JobBase(BaseModel):
    job_type: JobType
    owner_id: str
    person_id: Optional[str] = None
    document_id: Optional[str] = None
    evidence_id: Optional[str] = None
    care_event_id: Optional[str] = None
    idempotency_key: Optional[str] = None
    correlation_id: Optional[str] = None


class JobCreate(JobBase):
    pass


class JobUpdate(BaseModel):
    status: Optional[JobStatus] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    attempt_count: Optional[int] = None
    progress_stage: Optional[str] = None
    progress_percentage: Optional[int] = None
    result_ref: Optional[str] = None
    result_data: Optional[List] = None
    error_message: Optional[str] = None
    error_code: Optional[str] = None
    worker_id: Optional[str] = None
    leased_until: Optional[datetime] = None
    next_retry_at: Optional[datetime] = None


class JobResponse(JobBase):
    id: str
    status: JobStatus
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    attempt_count: int
    max_attempts: int
    progress_stage: Optional[str] = None
    progress_percentage: Optional[int] = None
    result_ref: Optional[str] = None
    result_data: Optional[List] = None
    error_message: Optional[str] = None
    error_code: Optional[str] = None
    worker_id: Optional[str] = None
    leased_until: Optional[datetime] = None
    next_retry_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class JobListResponse(BaseModel):
    jobs: List[JobResponse]
    total: int
    page: int
    size: int
    pages: int