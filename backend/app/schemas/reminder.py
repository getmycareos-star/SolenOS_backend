from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class CareReminderCreate(BaseModel):
    person_id: str
    title: str = Field(min_length=1, max_length=180)
    note: Optional[str] = Field(default=None, max_length=2000)
    due_at: datetime


class CareReminderComplete(BaseModel):
    completion_note: Optional[str] = Field(default=None, max_length=2000)


class CareReminderRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    person_id: str
    created_by_caregiver_id: str
    title: str
    note: Optional[str] = None
    due_at: datetime
    status: Literal["open", "completed", "cancelled"]
    completed_at: Optional[datetime] = None
    completed_by_caregiver_id: Optional[str] = None
    completion_note: Optional[str] = None
    cancelled_at: Optional[datetime] = None
    cancelled_by_caregiver_id: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None
    delivery_mode: Literal["in_app_list_only"] = "in_app_list_only"
    notification_delivery_status: Literal["not_sent"] = "not_sent"
