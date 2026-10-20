"""Pydantic schemas for Person Responsibility models in SolenOS.

Defines the request and response models for the person responsibility API endpoints.
"""

from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime
from app.core.handoff_enums import ResponsibilityStatus, ResponsibilityType
from app.core.caregiver_enums import CaregiverAvailabilityStatus


# Person Relationship Schemas
class PersonRelationshipCreate(BaseModel):
    person_id: str
    relationship_type: str = Field(..., description="e.g., 'daughter', 'spouse', 'friend', 'neighbor', 'professional'")
    relationship_description: Optional[str] = None
    is_principal: bool = Field(False, description="Whether this is the primary relationship")
    relationship_source: Optional[str] = None
    effective_start: Optional[datetime] = None
    effective_end: Optional[datetime] = None
    is_active: bool = True

    class Config:
        orm_mode = True


class PersonRelationshipResponse(BaseModel):
    id: str
    person_id: str
    relationship_type: str
    relationship_description: Optional[str]
    is_principal: bool
    relationship_source: Optional[str]
    effective_start: datetime
    effective_end: Optional[datetime]
    is_active: bool
    created_by_account_id: Optional[str]
    created_at: datetime
    updated_at: datetime

    class Config:
        orm_mode = True


# Person Role Schemas
class PersonRoleCreate(BaseModel):
    person_id: str
    role_type: str = Field(..., description="e.g., 'family caregiver', 'nurse', 'PT', 'OT', 'physician', 'pharmacist'")
    custom_role_name: Optional[str] = None
    role_description: Optional[str] = None
    is_principal: bool = Field(False, description="Whether this is the primary role")
    role_source: Optional[str] = None
    permissions: Optional[List[str]] = None
    restrictions: Optional[List[str]] = None
    effective_start: Optional[datetime] = None
    effective_end: Optional[datetime] = None
    is_active: bool = True

    class Config:
        orm_mode = True


class PersonRoleResponse(BaseModel):
    id: str
    person_id: str
    role_type: str
    custom_role_name: Optional[str]
    role_description: Optional[str]
    is_principal: bool
    role_source: Optional[str]
    permissions: Optional[List[str]]
    restrictions: Optional[List[str]]
    effective_start: datetime
    effective_end: Optional[datetime]
    is_active: bool
    created_by_account_id: Optional[str]
    created_at: datetime
    updated_at: datetime

    class Config:
        orm_mode = True


# Person Responsibility Schemas
class PersonResponsibilityCreate(BaseModel):
    person_id: str = Field(..., description="The person the responsibility is about (care recipient)")
    responsibility_type: ResponsibilityType
    custom_responsibility_type: Optional[str] = None
    title: str = Field(..., description="Short name of the responsibility")
    description: Optional[str] = None
    instructions: Optional[str] = None
    owner_account_id: Optional[str] = Field(None, description="Account ID of the person responsible (NULL for unassigned)")
    owner_person_id: Optional[str] = Field(None, description="Person ID of the person responsible")
    scope: Optional[str] = None
    permission_scope: Optional[str] = None
    effective_start: Optional[datetime] = None
    effective_end: Optional[datetime] = None
    expected_timing: Optional[str] = Field(None, description="When this should be done (e.g., 'daily', 'weekly')")
    frequency: Optional[int] = Field(None, description="How often (if applicable)")
    status: Optional[ResponsibilityStatus] = None
    depends_on: Optional[List[str]] = None
    source_evidence_ids: Optional[List[str]] = None
    provenance: Optional[str] = None
    is_active: bool = True

    class Config:
        orm_mode = True


class PersonResponsibilityResponse(BaseModel):
    id: str
    person_id: str
    responsibility_type: ResponsibilityType
    custom_responsibility_type: Optional[str]
    title: str
    description: Optional[str]
    instructions: Optional[str]
    owner_account_id: Optional[str]
    owner_person_id: Optional[str]
    scope: Optional[str]
    permission_scope: Optional[str]
    effective_start: datetime
    effective_end: Optional[datetime]
    expected_timing: Optional[str]
    frequency: Optional[int]
    status: ResponsibilityStatus
    depends_on: Optional[List[str]]
    source_evidence_ids: Optional[List[str]]
    provenance: Optional[str]
    completion_evidence_ids: Optional[List[str]]
    failure_reason: Optional[str]
    reassignment_history: Optional[List[dict]]
    verification_status: Optional[str]
    verified_by_account_id: Optional[str]
    verified_at: Optional[datetime]
    is_active: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        orm_mode = True


# Person Availability Schemas
class PersonAvailabilityCreate(BaseModel):
    person_id: str
    availability_status: CaregiverAvailabilityStatus
    availability_description: Optional[str] = None
    effective_start: Optional[datetime] = None
    effective_end: Optional[datetime] = None
    is_recurring: bool = Field(False, description="Whether this availability follows a recurring pattern")
    recurrence_pattern: Optional[str] = Field(None, description="e.g., 'weekdays 9am-5pm', 'every Monday'")
    timezone: str = Field("UTC", description="Timezone for the availability")
    preferred_contact_method: Optional[str] = None
    contact_information: Optional[dict] = None
    availability_source: Optional[str] = None
    verified_by_account_id: Optional[str] = None
    verified_at: Optional[datetime] = None
    is_active: bool = True

    class Config:
        orm_mode = True


class PersonAvailabilityResponse(BaseModel):
    id: str
    person_id: str
    availability_status: CaregiverAvailabilityStatus
    availability_description: Optional[str]
    effective_start: datetime
    effective_end: Optional[datetime]
    is_recurring: bool
    recurrence_pattern: Optional[str]
    timezone: str
    preferred_contact_method: Optional[str]
    contact_information: Optional[dict]
    availability_source: Optional[str]
    verified_by_account_id: Optional[str]
    verified_at: Optional[datetime]
    is_active: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        orm_mode = True


# Information Contribution Schemas
class InformationContributionCreate(BaseModel):
    person_id: str = Field(..., description="The person the information is about")
    contribution_type: str = Field(..., description="e.g., 'observation', 'measurement', 'decision', 'instruction'")
    contribution_description: str = Field(..., description="Description of what was contributed")
    evidence_ids: Optional[List[str]] = None
    related_evidence_ids: Optional[List[str]] = None
    contribution_context: Optional[str] = None
    contribution_source: Optional[str] = None
    contributed_at: Optional[datetime] = None
    effective_until: Optional[datetime] = None
    verification_status: Optional[str] = None
    verified_by_account_id: Optional[str] = None
    verified_at: Optional[datetime] = None
    is_active: bool = True

    class Config:
        orm_mode = True


class InformationContributionResponse(BaseModel):
    id: str
    person_id: str
    contributor_account_id: str
    contributor_person_id: Optional[str]
    contribution_type: str
    contribution_description: str
    evidence_ids: Optional[List[str]]
    related_evidence_ids: Optional[List[str]]
    contribution_context: Optional[str]
    contribution_source: Optional[str]
    contributed_at: datetime
    effective_until: Optional[datetime]
    verification_status: Optional[str]
    verified_by_account_id: Optional[str]
    verified_at: Optional[datetime]
    is_active: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        orm_mode = True