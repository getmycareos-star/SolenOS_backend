from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class CareCircleCreate(BaseModel):
    person_id: str
    name: str = Field(min_length=1, max_length=120)
    description: Optional[str] = Field(default=None, max_length=2000)


class CareCircleRead(BaseModel):
    id: str
    person_id: str
    name: str
    description: Optional[str] = None
    is_active: bool
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class CareCircleMemberCreate(BaseModel):
    member_caregiver_id: str
    role: str = "family_member"
    role_description: Optional[str] = Field(default=None, max_length=500)
    access_level: str = "contributor"
    relationship_to_person: Optional[str] = Field(default=None, max_length=100)


class CareCircleMembershipRead(BaseModel):
    id: str
    care_circle_id: str
    person_id: str
    member_caregiver_id: str
    role: str
    role_description: Optional[str] = None
    access_level: str
    can_view_information: bool
    can_contribute_information: bool
    can_receive_notifications: bool
    can_participate_in_decisions: bool
    relationship_to_person: Optional[str] = None
    invitation_status: str
    joined_at: datetime
    left_at: Optional[datetime] = None
    is_active: bool
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True
