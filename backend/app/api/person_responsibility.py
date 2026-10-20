"""Person Responsibility API endpoints for SolenOS.

Implements the foundational workflow for people, care teams, and responsibilities
as specified in the SolenOS core principles.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime, timezone
from app.core.database import get_db
from app.core.security import get_current_principal, require_authenticated, require_care_context_access, AuthenticatedPrincipal
from app.models.person_relationship import PersonRelationship
from app.models.person_role import PersonRole
from app.models.person_responsibility import PersonResponsibility
from app.models.person_availability import PersonAvailability
from app.models.information_contribution import InformationContribution
from app.models.identity import Person, Account
from app.schemas.person_responsibility import (
    PersonRelationshipCreate,
    PersonRelationshipResponse,
    PersonRoleCreate,
    PersonRoleResponse,
    PersonResponsibilityCreate,
    PersonResponsibilityResponse,
    PersonAvailabilityCreate,
    PersonAvailabilityResponse,
    InformationContributionCreate,
    InformationContributionResponse,
)
from app.services.notification import create_notification_service
from app.schemas.notification import NotificationCreate
from app.core.caregiver_enums import CaregiverAvailabilityStatus
from app.core.handoff_enums import ResponsibilityStatus, ResponsibilityType

router = APIRouter()


# Person Relationship Endpoints
@router.post("/person-relationships", response_model=PersonRelationshipResponse)
def create_person_relationship(
    relationship: PersonRelationshipCreate,
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    """Create a new person relationship."""
    # Authorization check: ensure the authenticated user can access the person
    require_care_context_access(principal, relationship.person_id, "create person relationship")
    
    db_relationship = PersonRelationship(
        person_id=relationship.person_id,
        relationship_type=relationship.relationship_type,
        relationship_description=relationship.relationship_description,
        is_principal=1 if relationship.is_principal else 0,
        relationship_source=relationship.relationship_source,
        effective_start=relationship.effective_start or datetime.now(timezone.utc),
        effective_end=relationship.effective_end,
        is_active=1 if relationship.is_active else 0,
        created_by_account_id=principal.account_id,
    )
    db.add(db_relationship)
    db.commit()
    db.refresh(db_relationship)
    return db_relationship


@router.get("/person-relationships/{person_id}", response_model=List[PersonRelationshipResponse])
def list_person_relationships(
    person_id: str,
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
    is_active: Optional[bool] = None,
    relationship_type: Optional[str] = None,
):
    """List relationships for a person."""
    # Authorization check
    require_care_context_access(principal, person_id, "list person relationships")
    
    query = db.query(PersonRelationship).filter(PersonRelationship.person_id == person_id)
    
    if is_active is not None:
        query = query.filter(PersonRelationship.is_active == (1 if is_active else 0))
    
    if relationship_type is not None:
        query = query.filter(PersonRelationship.relationship_type == relationship_type)
        
    return query.all()


# Person Role Endpoints
@router.post("/person-roles", response_model=PersonRoleResponse)
def create_person_role(
    role: PersonRoleCreate,
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    """Create a new person role."""
    # Authorization check
    require_care_context_access(principal, role.person_id, "create person role")
    
    db_role = PersonRole(
        person_id=role.person_id,
        role_type=role.role_type,
        custom_role_name=role.custom_role_name,
        role_description=role.role_description,
        is_principal=1 if role.is_principal else 0,
        role_source=role.role_source,
        permissions=role.permissions,
        restrictions=role.restrictions,
        effective_start=role.effective_start or datetime.now(timezone.utc),
        effective_end=role.effective_end,
        is_active=1 if role.is_active else 0,
        created_by_account_id=principal.account_id,
    )
    db.add(db_role)
    db.commit()
    db.refresh(db_role)
    return db_role


@router.get("/person-roles/{person_id}", response_model=List[PersonRoleResponse])
def list_person_roles(
    person_id: str,
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
    is_active: Optional[bool] = None,
    role_type: Optional[str] = None,
):
    """List roles for a person."""
    # Authorization check
    require_care_context_access(principal, person_id, "list person roles")
    
    query = db.query(PersonRole).filter(PersonRole.person_id == person_id)
    
    if is_active is not None:
        query = query.filter(PersonRole.is_active == (1 if is_active else 0))
    
    if role_type is not None:
        query = query.filter(PersonRole.role_type == role_type)
        
    return query.all()


# Person Responsibility Endpoints
@router.post("/person-responsibilities", response_model=PersonResponsibilityResponse)
def create_person_responsibility(
    responsibility: PersonResponsibilityCreate,
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    """Create a new person responsibility."""
    # Authorization check
    require_care_context_access(principal, responsibility.person_id, "create person responsibility")
    
    db_responsibility = PersonResponsibility(
        person_id=responsibility.person_id,
        responsibility_type=responsibility.responsibility_type,
        custom_responsibility_type=responsibility.custom_responsibility_type,
        title=responsibility.title,
        description=responsibility.description,
        instructions=responsibility.instructions,
        owner_account_id=responsibility.owner_account_id,
        owner_person_id=responsibility.owner_person_id,
        scope=responsibility.scope,
        permission_scope=responsibility.permission_scope,
        effective_start=responsibility.effective_start or datetime.now(timezone.utc),
        effective_end=responsibility.effective_end,
        expected_timing=responsibility.expected_timing,
        frequency=responsibility.frequency,
        status=responsibility.status or ResponsibilityStatus.PROPOSED,
        depends_on=responsibility.depends_on,
        source_evidence_ids=responsibility.source_evidence_ids,
        provenance=responsibility.provenance,
        is_active=1 if responsibility.is_active else 0,
        created_by_account_id=principal.account_id,
    )
    db.add(db_responsibility)
    db.commit()
    db.refresh(db_responsibility)
    return db_responsibility


@router.get("/person-responsibilities/{person_id}", response_model=List[PersonResponsibilityResponse])
def list_person_responsibilities(
    person_id: str,
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
    is_active: Optional[bool] = None,
    status: Optional[ResponsibilityStatus] = None,
    responsibility_type: Optional[ResponsibilityType] = None,
    owner_account_id: Optional[str] = None,
):
    """List responsibilities for a person."""
    # Authorization check
    require_care_context_access(principal, person_id, "list person responsibilities")
    
    query = db.query(PersonResponsibility).filter(PersonResponsibility.person_id == person_id)
    
    if is_active is not None:
        query = query.filter(PersonResponsibility.is_active == (1 if is_active else 0))
    
    if status is not None:
        query = query.filter(PersonResponsibility.status == status)
    
    if responsibility_type is not None:
        query = query.filter(PersonResponsibility.responsibility_type == responsibility_type)
        
    if owner_account_id is not None:
        query = query.filter(PersonResponsibility.owner_account_id == owner_account_id)
        
    return query.all()


@router.get("/person-responsibilities/{responsibility_id}", response_model=PersonResponsibilityResponse)
def get_person_responsibility(
    responsibility_id: str,
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    """Get a specific responsibility by ID."""
    responsibility = db.query(PersonResponsibility).filter(PersonResponsibility.id == responsibility_id).first()
    if not responsibility:
        raise HTTPException(status_code=404, detail="Responsibility not found")
    
    # Authorization check
    require_care_context_access(principal, responsibility.person_id, "get person responsibility")
    
    return responsibility


@router.patch("/person-responsibilities/{responsibility_id}/assign")
def assign_person_responsibility(
    responsibility_id: str,
    owner_account_id: str,
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    """Assign ownership of a responsibility."""
    responsibility = db.query(PersonResponsibility).filter(PersonResponsibility.id == responsibility_id).first()
    if not responsibility:
        raise HTTPException(status_code=404, detail="Responsibility not found")
    
    # Authorization check
    require_care_context_access(principal, responsibility.person_id, "assign person responsibility")
    
    # Record reassignment in history
    reassignment_entry = {
        "previous_owner_id": responsibility.owner_account_id,
        "new_owner_id": owner_account_id,
        "assigned_at": datetime.now(timezone.utc).isoformat(),
        "assigned_by": principal.account_id,
        "reason": None,  # Could be extended to accept a reason
    }
    
    history = responsibility.reassignment_history or []
    history.append(reassignment_entry)
    
    responsibility.owner_account_id = owner_account_id
    responsibility.reassignment_history = history
    responsibility.updated_at = datetime.now(timezone.utc)
    
    db.commit()
    db.refresh(responsibility)
    return {"message": "Responsibility assigned successfully"}


# Person Availability Endpoints
@router.post("/person-availabilities", response_model=PersonAvailabilityResponse)
def create_person_availability(
    availability: PersonAvailabilityCreate,
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    """Create a new person availability record."""
    # Authorization check
    require_care_context_access(principal, availability.person_id, "create person availability")
    
    db_availability = PersonAvailability(
        person_id=availability.person_id,
        availability_status=availability.availability_status,
        availability_description=availability.availability_description,
        effective_start=availability.effective_start or datetime.now(timezone.utc),
        effective_end=availability.effective_end,
        is_recurring=1 if availability.is_recurring else 0,
        recurrence_pattern=availability.recurrence_pattern,
        timezone=availability.timezone or "UTC",
        preferred_contact_method=availability.preferred_contact_method,
        contact_information=availability.contact_information,
        availability_source=availability.availability_source,
        verified_by_account_id=availability.verified_by_account_id,
        verified_at=availability.verified_at,
        is_active=1 if availability.is_active else 0,
        created_by_account_id=principal.account_id,
    )
    db.add(db_availability)
    db.commit()
    db.refresh(db_availability)
    return db_availability


@router.get("/person-availabilities/{person_id}", response_model=List[PersonAvailabilityResponse])
def list_person_availabilities(
    person_id: str,
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
    is_active: Optional[bool] = None,
    availability_status: Optional[CaregiverAvailabilityStatus] = None,
):
    """List availability records for a person."""
    # Authorization check
    require_care_context_access(principal, person_id, "list person availabilities")
    
    query = db.query(PersonAvailability).filter(PersonAvailability.person_id == person_id)
    
    if is_active is not None:
        query = query.filter(PersonAvailability.is_active == (1 if is_active else 0))
    
    if availability_status is not None:
        query = query.filter(PersonAvailability.availability_status == availability_status)
        
    return query.all()


# Information Contribution Endpoints
@router.post("/information-contributions", response_model=InformationContributionResponse)
def create_information_contribution(
    contribution: InformationContributionCreate,
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    """Record a new information contribution."""
    # Authorization check
    require_care_context_access(principal, contribution.person_id, "create information contribution")
    
    db_contribution = InformationContribution(
        person_id=contribution.person_id,
        contributor_account_id=principal.account_id,  # Always use the authenticated user as contributor
        contributor_person_id=contribution.contributor_person_id,
        contribution_type=contribution.contribution_type,
        contribution_description=contribution.contribution_description,
        evidence_ids=contribution.evidence_ids,
        related_evidence_ids=contribution.related_evidence_ids,
        contribution_context=contribution.contribution_context,
        contribution_source=contribution.contribution_source,
        contributed_at=contribution.contributed_at or datetime.now(timezone.utc),
        effective_until=contribution.effective_until,
        verification_status=contribution.verification_status,
        verified_by_account_id=contribution.verified_by_account_id,
        verified_at=contribution.verified_at,
        is_active=1 if contribution.is_active else 0,
        created_by_account_id=principal.account_id,
    )
    db.add(db_contribution)
    db.commit()
    db.refresh(db_contribution)
    return db_contribution


@router.get("/information-contributions/{person_id}", response_model=List[InformationContributionResponse])
def list_information_contributions(
    person_id: str,
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
    is_active: Optional[bool] = None,
    contribution_type: Optional[str] = None,
    contributor_account_id: Optional[str] = None,
):
    """List information contributions for a person."""
    # Authorization check
    require_care_context_access(principal, person_id, "list information contributions")
    
    query = db.query(InformationContribution).filter(InformationContribution.person_id == person_id)
    
    if is_active is not None:
        query = query.filter(InformationContribution.is_active == (1 if is_active else 0))
    
    if contribution_type is not None:
        query = query.filter(InformationContribution.contribution_type == contribution_type)
        
    if contributor_account_id is not None:
        query = query.filter(InformationContribution.contributor_account_id == contributor_account_id)
        
    return query.order_by(InformationContribution.contributed_at.desc()).all()


# Special endpoints for responsibility queries
@router.get("/person-responsibilities/unassigned/{person_id}", response_model=List[PersonResponsibilityResponse])
def get_unassigned_responsibilities(
    person_id: str,
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    """Get all unassigned responsibilities for a person."""
    # Authorization check
    require_care_context_access(principal, person_id, "get unassigned responsibilities")
    
    return db.query(PersonResponsibility).filter(
        PersonResponsibility.person_id == person_id,
        PersonResponsibility.owner_account_id == None,
        PersonResponsibility.is_active == 1
    ).all()


@router.get("/person-responsibilities/active/{person_id}", response_model=List[PersonResponsibilityResponse])
def get_active_responsibilities(
    person_id: str,
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    """Get all active responsibilities for a person."""
    # Authorization check
    require_care_context_access(principal, person_id, "get active responsibilities")
    
    return db.query(PersonResponsibility).filter(
        PersonResponsibility.person_id == person_id,
        PersonResponsibility.status == ResponsibilityStatus.ACTIVE,
        PersonResponsibility.is_active == 1
    ).all()


@router.get("/person-responsibilities/failed/{person_id}", response_model=List[PersonResponsibilityResponse])
def get_failed_responsibilities(
    person_id: str,
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    """Get all failed/responsibilities with issues for a person."""
    # Authorization check
    require_care_context_access(principal, person_id, "get failed responsibilities")
    
    return db.query(PersonResponsibility).filter(
        PersonResponsibility.person_id == person_id,
        PersonResponsibility.status.in_([
            ResponsibilityStatus.DECLINED,
            ResponsibilityStatus.TRANSFERRED
        ]),
        PersonResponsibility.is_active == 1
    ).all()
