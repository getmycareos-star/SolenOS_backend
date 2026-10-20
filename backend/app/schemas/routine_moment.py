"""Pydantic schemas for Routine Moment API."""

from pydantic import BaseModel, Field
from typing import Optional, Dict, Any
from datetime import datetime
from app.schemas.base import BaseSchema


class RoutineMomentBase(BaseSchema):
    routine_name: str = Field(..., example="Morning Routine")
    observation_text: str = Field(..., example="Took medication at 8:00 AM")
    observation_data: Optional[Dict[str, Any]] = Field(None, example={"time": "08:00", "assistance_level": "independent"})
    observed_at: datetime = Field(..., example="2026-10-08T08:00:00Z")
    confidence: Optional[float] = Field(None, example=0.95)
    status: Optional[str] = Field("active", example="active")


class RoutineMomentCreate(RoutineMomentBase):
    evidence_id: Optional[str] = Field(None, example="evidence-uuid")
    care_event_id: Optional[str] = Field(None, example="care-event-uuid")


class RoutineMomentUpdate(BaseModel):
    routine_name: Optional[str] = None
    observation_text: Optional[str] = None
    observation_data: Optional[Dict[str, Any]] = None
    observed_at: Optional[datetime] = None
    confidence: Optional[float] = None
    status: Optional[str] = None
    evidence_id: Optional[str] = None
    care_event_id: Optional[str] = None


class RoutineMomentInDBBase(RoutineMomentBase):
    id: str
    version: int
    person_id: str
    evidence_id: Optional[str] = None
    care_event_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    created_by_caregiver_id: Optional[str] = None

    class Config:
        orm_mode = True


class RoutineMoment(RoutineMomentInDBBase):
    pass


class RoutineMomentInDB(RoutineMomentInDBBase):
    pass