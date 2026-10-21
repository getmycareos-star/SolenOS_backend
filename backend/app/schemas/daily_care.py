from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class DailyCareEntryCreate(BaseModel):
    person_id: str
    caregiver_id: str
    mood: str | None = None
    mood_scale: int | None = None
    agitation_level: str | None = None
    agitation_scale: int | None = None
    vitals: Any = Field(default_factory=dict)
    behavior_notes: str | None = None
    sleep_notes: str | None = None
    eating_notes: str | None = None
    wellbeing_stress: int | None = None
    wellbeing_notes: str | None = None
    general_notes: str | None = None
    location: dict[str, Any] | None = None
    location_provenance: str | None = None
    medications: list[dict[str, Any]] = Field(default_factory=list)
    scales: list[dict[str, Any]] = Field(default_factory=list)
    notes: str | None = None


class DailyCareEntryRead(DailyCareEntryCreate):
    id: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
