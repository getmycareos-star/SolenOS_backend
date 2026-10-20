"""Care Circle schemas for SolenOS API.

Defines the request/response models for Care Circle endpoints.
"""

from pydantic import BaseModel, Field, validator
from typing import Optional, List, Dict, Any
from datetime import datetime
from enum import Enum


class CareCircleRole(str, Enum):
    """Roles within a care circle."""
    PRIMARY_FAMILY = "primary_family"
    SECONDARY_FAMILY = "secondary_family"
    FRIEND = "friend"
    NEIGHBOR = "neighbor"
    VOLUNTEER = "volunteer"
    PROFESSIONAL_CAREGIVER = "professional_caregiver"
    CARE_COORDINATOR = "care_coordinator"
    SOCIAL_WORKER = "social_worker"
    THERAPIST = "therapist"
    DOCTOR = "doctor"
    NURSE = "nurse"
    OTHER = "other"


class CareCircleAccessLevel(str, Enum):
    """Access levels within a care circle."""
    NONE = "none"
    LIMITED = "limited"
    STANDARD = "standard"
    FULL = "full"
    ADMINISTRATIVE = "administrative"


class CareCircleInvitationStatus(str, Enum):
    """Invitation status for care circle membership."""
    PENDING = "pending"
    ACCEPTED = "accepted"
    DECLINED = "declined"
    EXPIRED = "expired"


class CareCircleBase(BaseModel):
    """Base care circle model."""
    person_id: str
    name: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = Field(None, max_length=1000)


class CareCircleCreate(CareCircleBase):
    """Model for creating a new care circle."""
    pass


class CareCircleUpdate(BaseModel):
    """Model for updating a care circle."""
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = Field(None, max_length=1000)
    is_active: Optional[bool] = None


class CareCircleResponse(CareCircleBase):
    """Model for care circle response."""
    id: str
    is_active: bool
    created_at: datetime
    updated_at: Optional[datetime] = None
    
    class Config:
        from_attributes = True


class CareCircleMembershipBase(BaseModel):
    """Base care circle membership model."""
    care_circle_id: str
    member_person_id: Optional[str] = None
    member_account_id: Optional[str] = None
    role: CareCircleRole
    role_description: Optional[str] = Field(None, max_length=500)
    access_level: CareCircleAccessLevel = CareCircleAccessLevel.LIMITED
    can_view_information: bool = False
    can_contribute_information: bool = False
    can_receive_notifications: bool = False
    can_participate_in_decisions: bool = False
    relationship_to_person: Optional[str] = Field(None, max_length=100)
    contact_information: Optional[Dict[str, Any]] = None


class CareCircleMembershipCreate(CareCircleMembershipBase):
    """Model for creating a new care circle membership."""
    @validator('member_person_id', 'member_account_id')
    def at_least_one_member_specified(cls, v, values, **kwargs):
        # Ensure either person_id or account_id is specified
        field_name = kwargs['field']['name']
        if field_name == 'member_person_id':
            account_id = values.get('member_account_id')
            if not v and not account_id:
                raise ValueError('Either member_person_id or member_account_id must be specified')
        elif field_name == 'member_account_id':
            person_id = values.get('member_person_id')
            if not v and not person_id:
                raise ValueError('Either member_person_id or member_account_id must be specified')
        return v


class CareCircleMembershipUpdate(BaseModel):
    """Model for updating a care circle membership."""
    role: Optional[CareCircleRole] = None
    role_description: Optional[str] = Field(None, max_length=500)
    access_level: Optional[CareCircleAccessLevel] = None
    can_view_information: Optional[bool] = None
    can_contribute_information: Optional[bool] = None
    can_receive_notifications: Optional[bool] = None
    can_participate_in_decisions: Optional[bool] = None
    relationship_to_person: Optional[str] = Field(None, max_length=100)
    contact_information: Optional[Dict[str, Any]] = None
    is_active: Optional[bool] = None


class CareCircleMembershipResponse(CareCircleMembershipBase):
    """Model for care circle membership response."""
    id: str
    joined_at: datetime
    left_at: Optional[datetime] = None
    is_active: bool
    invitation_status: CareCircleInvitationStatus
    invited_by_account_id: Optional[str] = None
    invited_at: Optional[datetime] = None
    responded_at: Optional[datetime] = None
    created_at: datetime
    updated_at: Optional[datetime] = None
    
    class Config:
        from_attributes = True


class CareCircleInvitationCreate(BaseModel):
    """Model for creating a care circle invitation."""
    care_circle_id: str
    member_person_id: Optional[str] = None
    member_account_id: Optional[str] = None
    role: CareCircleRole
    role_description: Optional[str] = Field(None, max_length=500)
    access_level: CareCircleAccessLevel = CareCircleAccessLevel.LIMITED
    relationship_to_person: Optional[str] = Field(None, max_length=100)
    contact_information: Optional[Dict[str, Any]] = None


class CareCircleInvitationResponse(BaseModel):
    """Model for care circle invitation response."""
    id: str
    care_circle_id: str
    care_circle_name: str
    member_person_id: Optional[str] = None
    member_account_id: Optional[str] = None
    member_person_name: Optional[str] = None
    member_account_email: Optional[str] = None
    role: CareCircleRole
    access_level: CareCircleAccessLevel
    invitation_status: CareCircleInvitationStatus
    invited_at: datetime
    responded_at: Optional[datetime] = None
    
    class Config:
        from_attributes = True


class CareCircleWithMemberships(CareCircleResponse):
    """Care circle with its memberships."""
    memberships: List[CareCircleMembershipResponse] = []
    
    class Config:
        from_attributes = True