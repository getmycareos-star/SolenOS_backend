from datetime import datetime
from typing import Optional, Literal
from pydantic import BaseModel, Field, field_validator


class ReminderCreate(BaseModel):
    title: str = Field(min_length=1, max_length=180)
    message: str = Field(min_length=1, max_length=2000)
    scheduled_at: datetime
    timezone: str = Field(min_length=1, max_length=80)
    recurrence: Literal["once", "daily", "weekly"] = "once"
    local_time: str
    max_attempts: int = Field(default=5, ge=1, le=10)
    retry_delay_seconds: int = Field(default=60, ge=10, le=3600)

    @field_validator("title", "message")
    @classmethod
    def require_nonblank_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("title and message must not be blank")
        return normalized

    @field_validator("scheduled_at")
    @classmethod
    def require_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("scheduled_at must include a timezone offset")
        return value

    @field_validator("local_time")
    @classmethod
    def validate_local_time(cls, value: str) -> str:
        from datetime import time
        try:
            parsed = time.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("local_time must use HH:MM format") from exc
        if parsed.second or parsed.microsecond:
            raise ValueError("local_time must use HH:MM precision")
        return parsed.strftime("%H:%M")


class ReminderOut(BaseModel):
    id: str
    person_id: str
    action_id: str
    title: str
    message: str
    timezone: str
    recurrence: str
    next_run_at: Optional[datetime] = None
    local_time: str
    enabled: bool
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class ReminderDeliveryOut(BaseModel):
    id: str
    reminder_id: str
    person_id: str
    idempotency_key: str
    scheduled_for: datetime
    status: str
    attempt_count: int
    next_attempt_at: datetime
    last_error: Optional[str] = None
    delivered_at: Optional[datetime] = None
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True



class ReminderDeliveryAttemptOut(BaseModel):
    id: str
    delivery_id: str
    attempt_number: int
    channel: str
    started_at: datetime
    finished_at: Optional[datetime] = None
    outcome: str
    error_type: Optional[str] = None
    idempotency_key: str

    class Config:
        from_attributes = True
