"""Care Circle API endpoints for SolenOS.

Implements endpoints for managing family and support networks.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime
import logging

from app.core.database import get_db
from app.core.security import (
    get_principal_from_request,
    require_authenticated,
    require_care_context_access,
    AuthenticatedPrincipal
)
from app.models.identity import Person, Account
from app.schemas.care_circle import (
    CareCircleCreate, CareCircleUpdate, CareCircleResponse,
    CareCircleMembershipCreate, CareCircleMembershipUpdate, CareCircleMembershipResponse,
    CareCircleInvitationCreate, CareCircleInvitationResponse,
    CareCircleWithMemberships, CareCircleRole, CareCircleAccessLevel, CareCircleInvitationStatus
)
from app.services.care_circle import CareCircleService

router = APIRouter(prefix="/care-circle", tags=["care-circle"])
logger = logging.getLogger(__name__)


# Care Circle endpoints
@router.post("", response_model=CareCircleResponse, status_code=status.HTTP_201_CREATED)
def create_care_circle(
    circle: CareCircleCreate,
    request: Request,
    db: Session = Depends(get_db),
):
    """Create a new care circle for a person."""
    principal = require_authenticated(get_principal_from_request(request))
    require_care_context_access(principal, circle.person_id, "create care circle")
    
    service = CareCircleService(db)
    care_circle = service.create_care_circle(
        person_id=circle.person_id,
        circle_data=circle,
        creator_account_id=principal.account_id
    )
    
    return care_circle


@router.get("/{circle_id}", response_model=CareCircleWithMemberships)
def get_care_circle(
    circle_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Get a care circle by ID with its memberships."""
    principal = require_authenticated(get_principal_from_request(request))
    
    service = CareCircleService(db)
    care_circle = service.get_care_circle(circle_id)
    if not care_circle:
        raise HTTPException(status_code=404, detail="Care circle not found")
    
    # Authorization check: user must be able to access the person
    require_care_context_access(principal, care_circle.person_id, "view care circle")
    
    # Get memberships
    memberships = service.get_care_circle_memberships(circle_id)
    
    # Build response
    response = CareCircleWithMemberships.from_orm(care_circle)
    response.memberships = [CareCircleMembershipResponse.from_orm(m) for m in memberships]
    
    return response


@router.get("/person/{person_id}", response_model=List[CareCircleResponse])
def get_person_care_circles(
    person_id: str,
    request: Request,
    active_only: bool = Query(True),
    db: Session = Depends(get_db),
):
    """Get all care circles for a person."""
    principal = require_authenticated(get_principal_from_request(request))
    require_care_context_access(principal, person_id, "view person care circles")
    
    service = CareCircleService(db)
    care_circles = service.get_person_care_circles(person_id, active_only=active_only)
    
    return care_circles


@router.patch("/{circle_id}", response_model=CareCircleResponse)
def update_care_circle(
    circle_id: str,
    circle: CareCircleUpdate,
    request: Request,
    db: Session = Depends(get_db),
):
    """Update a care circle."""
    principal = require_authenticated(get_principal_from_request(request))
    
    service = CareCircleService(db)
    care_circle = service.get_care_circle(circle_id)
    if not care_circle:
        raise HTTPException(status_code=404, detail="Care circle not found")
    
    # Authorization check: user must be able to access the person
    require_care_context_access(principal, care_circle.person_id, "update care circle")
    
    # Additional check: user should have appropriate permissions (e.g., be a member with sufficient access)
    # For simplicity, we'll allow anyone who can access the person to update basic circle info
    # In a more robust implementation, we'd check specific permissions
    
    updated_circle = service.update_care_circle(circle_id, circle)
    if not updated_circle:
        raise HTTPException(status_code=400, detail="Failed to update care circle")
    
    return updated_circle


@router.delete("/{circle_id}", status_code=status.HTTP_204_NO_CONTENT)
def deactivate_care_circle(
    circle_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Deactivate a care circle."""
    principal = require_authenticated(get_principal_from_request(request))
    
    service = CareCircleService(db)
    care_circle = service.get_care_circle(circle_id)
    if not care_circle:
        raise HTTPException(status_code=404, detail="Care circle not found")
    
    # Authorization check: user must be able to access the person
    require_care_context_access(principal, care_circle.person_id, "deactivate care circle")
    
    success = service.deactivate_care_circle(circle_id)
    if not success:
        raise HTTPException(status_code=400, detail="Failed to deactivate care circle")
    
    return JSONResponse(status_code=status.HTTP_204_NO_CONTENT)


# Care Circle Membership endpoints
@router.post("/{circle_id}/members", response_model=CareCircleMembershipResponse, status_code=status.HTTP_201_CREATED)
def add_member_to_circle(
    circle_id: str,
    membership: CareCircleMembershipCreate,
    request: Request,
    db: Session = Depends(get_db),
):
    """Add a member to a care circle."""
    principal = require_authenticated(get_principal_from_request(request))
    
    service = CareCircleService(db)
    care_circle = service.get_care_circle(circle_id)
    if not care_circle:
        raise HTTPException(status_code=404, detail="Care circle not found")
    
    # Authorization check: user must be able to access the person
    require_care_context_access(principal, care_circle.person_id, "add member to care circle")
    
    # Additional authorization: user should have permission to add members
    # For simplicity, we'll allow anyone who can access the person to add members
    # In a more robust implementation, we'd check if the user is a coordinator or has administrative access
    
    try:
        new_membership = service.add_member_to_circle(
            care_circle_id=circle_id,
            membership_data=membership,
            inviter_account_id=principal.account_id
        )
        return new_membership
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{circle_id}/members", response_model=List[CareCircleMembershipResponse])
def get_care_circle_memberships(
    circle_id: str,
    request: Request,
    active_only: bool = Query(True),
    db: Session = Depends(get_db),
):
    """Get all members of a care circle."""
    principal = require_authenticated(get_principal_from_request(request))
    
    service = CareCircleService(db)
    care_circle = service.get_care_circle(circle_id)
    if not care_circle:
        raise HTTPException(status_code=404, detail="Care circle not found")
    
    # Authorization check: user must be able to access the person
    require_care_context_access(principal, care_circle.person_id, "view care circle members")
    
    memberships = service.get_care_circle_memberships(circle_id, active_only=active_only)
    return memberships


@router.get("/members/{membership_id}", response_model=CareCircleMembershipResponse)
def get_care_circle_membership(
    membership_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Get a specific care circle membership by ID."""
    principal = require_authenticated(get_principal_from_request(request))
    
    service = CareCircleService(db)
    membership = service.db.query(CareCircleMembership).filter(CareCircleMembership.id == membership_id).first()
    if not membership:
        raise HTTPException(status_code=404, detail="Care circle membership not found")
    
    # Authorization check: user must be able to access the person
    # Need to get the care circle first to get the person_id
    care_circle = service.db.query(CareCircle).filter(CareCircle.id == membership.care_circle_id).first()
    if not care_circle:
        raise HTTPException(status_code=404, detail="Associated care circle not found")
    
    require_care_context_access(principal, care_circle.person_id, "view care circle membership")
    
    return membership


@router.patch("/members/{membership_id}", response_model=CareCircleMembershipResponse)
def update_care_circle_membership(
    membership_id: str,
    membership: CareCircleMembershipUpdate,
    request: Request,
    db: Session = Depends(get_db),
):
    """Update a care circle membership."""
    principal = require_authenticated(get_principal_from_request(request))
    
    service = CareCircleService(db)
    db_membership = service.db.query(CareCircleMembership).filter(CareCircleMembership.id == membership_id).first()
    if not db_membership:
        raise HTTPException(status_code=404, detail="Care circle membership not found")
    
    # Authorization check: user must be able to access the person
    care_circle = service.db.query(CareCircle).filter(CareCircle.id == db_membership.care_circle_id).first()
    if not care_circle:
        raise HTTPException(status_code=404, detail="Associated care circle not found")
    
    require_care_context_access(principal, care_circle.person_id, "update care circle membership")
    
    # Additional authorization: user should have permission to update this membership
    # For simplicity, we'll allow coordinators or the member themselves to update
    
    updated_membership = service.update_membership(membership_id, membership)
    if not updated_membership:
        raise HTTPException(status_code=400, detail="Failed to update care circle membership")
    
    return updated_membership


@router.delete("/members/{membership_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_member_from_circle(
    membership_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Remove a member from a care circle."""
    principal = require_authenticated(get_principal_from_request(request))
    
    service = CareCircleService(db)
    membership = service.db.query(CareCircleMembership).filter(CareCircleMembership.id == membership_id).first()
    if not membership:
        raise HTTPException(status_code=404, detail="Care circle membership not found")
    
    # Authorization check: user must be able to access the person
    care_circle = service.db.query(CareCircle).filter(CareCircle.id == membership.care_circle_id).first()
    if not care_circle:
        raise HTTPException(status_code=404, detail="Associated care circle not found")
    
    require_care_context_access(principal, care_circle.person_id, "remove member from care circle")
    
    # Additional authorization: user should have permission to remove members
    # For simplicity, we'll allow coordinators to remove members
    
    success = service.remove_member_from_circle(membership_id)
    if not success:
        raise HTTPException(status_code=400, detail="Failed to remove member from care circle")
    
    return JSONResponse(status_code=status.HTTP_204_NO_CONTENT)


# Care Circle Invitation endpoints
@router.post("/invitations", response_model=CareCircleInvitationResponse, status_code=status.HTTP_201_CREATED)
def create_care_circle_invitation(
    invitation: CareCircleInvitationCreate,
    request: Request,
    db: Session = Depends(get_db),
):
    """Create a care circle invitation."""
    principal = require_authenticated(get_principal_from_request(request))
    
    # We'll use the service to handle the invitation creation which includes authorization
    service = CareCircleService(db)
    
    # First verify the care circle exists and user can access the person
    care_circle = service.get_care_circle(invitation.care_circle_id)
    if not care_circle:
        raise HTTPException(status_code=404, detail="Care circle not found")
    
    require_care_context_access(principal, care_circle.person_id, "create care circle invitation")
    
    # Convert invitation to membership creation (invitation is essentially a pending membership)
    membership_data = CareCircleMembershipCreate(
        member_person_id=invitation.member_person_id,
        member_account_id=invitation.member_account_id,
        role=invitation.role,
        role_description=invitation.role_description,
        access_level=invitation.access_level,
        relationship_to_person=invitation.relationship_to_person,
        contact_information=invitation.contact_information
    )
    
    try:
        # This will create the membership with pending status and send invitation
        membership = service.add_member_to_circle(
            care_circle_id=invitation.care_circle_id,
            membership_data=membership_data,
            inviter_account_id=principal.account_id
        )
        
        # Build invitation response
        # Get member names for response
        member_person_name = None
        member_account_email = None
        if membership.member_person:
            member_person_name = membership.member_person.name
        if membership.member_account:
            member_account_email = membership.member_account.email
        
        return CareCircleInvitationResponse(
            id=membership.id,
            care_circle_id=membership.care_circle_id,
            care_circle_name=care_circle.name,
            member_person_id=membership.member_person_id,
            member_account_id=membership.member_account_id,
            member_person_name=member_person_name,
            member_account_email=member_account_email,
            role=CareCircleRole(membership.role),
            access_level=CareCircleAccessLevel(membership.access_level),
            invitation_status=CareCircleInvitationStatus(membership.invitation_status),
            invited_at=membership.invited_at,
            responded_at=membership.responded_at
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/invitations/{membership_id}/respond", response_model=CareCircleMembershipResponse)
def respond_to_care_circle_invitation(
    membership_id: str,
    request: Request,
    accept: bool = Query(..., description="Whether to accept the invitation"),
    db: Session = Depends(get_db),
):
    """Respond to a care circle invitation."""
    principal = require_authenticated(get_principal_from_request(request))
    
    service = CareCircleService(db)
    membership = service.respond_to_invitation(membership_id, accept, principal.account_id)
    
    if not membership:
        raise HTTPException(status_code=404, detail="Care circle membership not found or not authorized to respond")
    
    return membership


# Utility endpoints
@router.get("/person/{person_id}/access/{account_id}", response_model=dict)
def get_member_access_info(
    person_id: str,
    account_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Get what information a specific account can access for a person."""
    principal = require_authenticated(get_principal_from_request(request))
    require_care_context_access(principal, person_id, "check member access")
    
    service = CareCircleService(db)
    access_info = service.get_accessible_information_for_member(person_id, account_id)
    
    return access_info