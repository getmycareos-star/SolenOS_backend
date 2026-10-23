from datetime import datetime
from typing import Any, List, Literal, Optional

from pydantic import BaseModel, Field, ConfigDict, model_validator


class CareHandoffCreate(BaseModel):
    person_id: str = Field(min_length=1, max_length=128)
    assigned_to_caregiver_id: Optional[str] = Field(default=None, max_length=128)
    title: str = Field(min_length=1, max_length=180)
    summary: str = Field(min_length=1, max_length=10000)
    outstanding_items: List[str] = Field(default_factory=list, max_length=100)
    recent_changes: List[str] = Field(default_factory=list, max_length=100)
    safety_concerns: List[str] = Field(default_factory=list, max_length=100)
    due_at: Optional[datetime] = None
    reminder_at: Optional[datetime] = None
    access_scope: Literal["care_circle", "assigned_only"] = "care_circle"

    @model_validator(mode="after")
    def validate_schedule(self):
        if self.reminder_at and not self.due_at:
            raise ValueError("A reminder time requires a due date")
        if self.reminder_at and self.due_at and self.reminder_at > self.due_at:
            raise ValueError("The reminder must be scheduled at or before the due date")
        return self


class CareHandoffCompletion(BaseModel):
    completion_report: str = Field(min_length=1, max_length=5000)


class CareHandoffRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    person_id: str
    created_by_caregiver_id: str
    assigned_to_caregiver_id: Optional[str] = None
    title: str
    summary: str
    outstanding_items: List[str]
    recent_changes: List[str]
    safety_concerns: List[str]
    status: str
    access_scope: str = "care_circle"
    due_at: Optional[datetime] = None
    reminder_at: Optional[datetime] = None
    completion_report: Optional[str] = None
    completed_by_caregiver_id: Optional[str] = None
    completed_at: Optional[datetime] = None
    acknowledged_by_caregiver_id: Optional[str] = None
    acknowledged_at: Optional[datetime] = None
    closed_by_caregiver_id: Optional[str] = None
    closed_at: Optional[datetime] = None
    created_at: datetime
    updated_at: Optional[datetime] = None


class CareHandoffEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    handoff_id: str
    person_id: str
    actor_caregiver_id: str
    event_type: str
    details: Any
    created_at: datetime
