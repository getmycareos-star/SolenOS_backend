from datetime import datetime
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field, ConfigDict


class AppointmentWorkspaceCreate(BaseModel):
    appointment_id: Optional[str] = None
    status: str = Field(default="preparation", max_length=40)
    payload: Dict[str, Any] = Field(default_factory=dict)
    change_reason: Optional[str] = Field(default="Initial appointment workspace save", max_length=2000)


class AppointmentWorkspaceUpdate(BaseModel):
    expected_version: int = Field(ge=1)
    status: Optional[str] = Field(default=None, max_length=40)
    payload: Dict[str, Any]
    change_reason: str = Field(min_length=1, max_length=2000)


class AppointmentWorkspaceRecordOut(BaseModel):
    id: str
    appointment_id: Optional[str]
    status: str
    version_number: int
    payload: Dict[str, Any]
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
