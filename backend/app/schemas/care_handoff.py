from datetime import datetime
from typing import Any, List, Optional

from pydantic import BaseModel, Field, ConfigDict


class CareHandoffCreate(BaseModel):
    person_id: str = Field(min_length=1, max_length=128)
    assigned_to_caregiver_id: Optional[str] = Field(default=None, max_length=128)
    title: str = Field(min_length=1, max_length=180)
    summary: str = Field(min_length=1, max_length=10000)
    outstanding_items: List[str] = Field(default_factory=list, max_length=100)
    recent_changes: List[str] = Field(default_factory=list, max_length=100)
    safety_concerns: List[str] = Field(default_factory=list, max_length=100)


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
