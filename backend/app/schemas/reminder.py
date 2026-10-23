from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field, field_validator


class ReminderCreate(BaseModel):
    person_id: str = Field(min_length=1)
    caregiver_id: str = Field(min_length=1)
    action_id: Optional[str] = None
    title: str = Field(min_length=1, max_length=200)
    message: str = Field(min_length=1, max_length=4000)
    due_at: datetime
    timezone: str = Field(default="UTC", min_length=1, max_length=80)
    idempotency_key: str = Field(min_length=8, max_length=200)
    max_attempts: int = Field(default=5, ge=1, le=10)

    @field_validator("due_at")
    @classmethod
    def require_timezone(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("due_at must include an explicit timezone offset")
        return value


class ReminderResponse(BaseModel):
    id: str
    person_id: str
    action_id: Optional[str]
    created_by_caregiver_id: str
    title: str
    message: str
    due_at: datetime
    timezone: str
    status: str
    idempotency_key: str
    attempt_count: int
    max_attempts: int
    next_attempt_at: Optional[datetime]
    last_error: Optional[str]
    delivered_at: Optional[datetime]
    cancelled_at: Optional[datetime]
    created_at: datetime
    updated_at: Optional[datetime]

    class Config:
        from_attributes = True
