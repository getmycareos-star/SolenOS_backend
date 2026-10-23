from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field, field_validator
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class ReminderCreate(BaseModel):
    person_id: str = Field(min_length=1)
    caregiver_id: str = Field(min_length=1)
    action_id: Optional[str] = None
    title: str = Field(min_length=1, max_length=200)
    message: str = Field(min_length=1, max_length=4000)
    due_at: datetime
    timezone: str = Field(default="UTC", min_length=1, max_length=80)
    recurrence_rule: Optional[str] = None
    idempotency_key: str = Field(min_length=8, max_length=200)
    max_attempts: int = Field(default=5, ge=1, le=10)

    @field_validator("timezone")
    @classmethod
    def require_known_timezone(cls, value):
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("timezone must be a valid IANA timezone") from exc
        return value

    @field_validator("recurrence_rule")
    @classmethod
    def validate_recurrence(cls, value):
        if value is not None and value not in {"daily", "weekly", "monthly"}:
            raise ValueError("recurrence_rule must be daily, weekly, monthly, or omitted")
        return value

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
    recurrence_rule: Optional[str] = None
    recurrence_anchor_day: Optional[int] = None
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
