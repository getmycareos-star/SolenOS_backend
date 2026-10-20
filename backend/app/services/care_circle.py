"""Care Circle service for SolenOS.

Implements business logic for managing family and support networks.
"""

from sqlalchemy.orm import Session
from sqlalchemy import and_, or_
from typing import List, Optional, Dict, Any
from uuid import uuid4
from datetime import datetime, timezone

from app.models.care_circle import CareCircle, CareCircleMembership
from app.models.identity import Person, Account
from app.schemas.care_circle import (
    CareCircleCreate, CareCircleUpdate, CareCircleResponse,
    CareCircleMembershipCreate, CareCircleMembershipUpdate, CareCircleMembershipResponse,
    CareCircleInvitationCreate, CareCircleInvitationResponse,
    CareCircleRole, CareCircleAccessLevel, CareCircleInvitationStatus
)
from app.core.database import utc_now
from app.services.notification import create_notification_service
from app.schemas.notification import NotificationCreate, NotificationType


class CareCircleService:
    """Service for managing care circles (family and support networks)."""
    
    def __init__(self, db: Session):
        self.db = db
    
    # Care Circle CRUD operations
    def create_care_circle(self, person_id: str, circle_data: CareCircleCreate, creator_account_id: str) -> CareCircle:
        """Create a new care circle for a person."""
        # Verify the person exists
        person = self.db.query(Person).filter(Person.id == person_id).first()
        if not person:
            raise ValueError(f"Person with ID {person_id} not found")
        
        # Create the care circle
        care_circle = CareCircle(
            id=str(uuid4()),
            person_id=person_id,
            name=circle_data.name,
            description=circle_data.description,
            is_active=1 if circle_data.is_active else 0 if hasattr(circle_data, 'is_active') else 1,
            created_by_account_id=creator_account_id
        )
        
        self.db.add(care_circle)
        self.db.commit()
        self.db.refresh(care_circle)
        
        # Automatically add the creator as a care coordinator if they're an account
        creator_account = self.db.query(Account).filter(Account.id == creator_account_id).first()
        if creator_account:
            self.add_member_to_circle(
                care_circle_id=care_circle.id,
                membership_data=CareCircleMembershipCreate(
                    member_account_id=creator_account_id,
                    role=CareCircleRole.CARE_COORDINATOR,
                    role_description="Created the care circle",
                    access_level=CareCircleAccessLevel.ADMINISTRATIVE,
                    can_view_information=True,
                    can_contribute_information=True,
                    can_receive_notifications=True,
                    can_participate_in_decisions=True
                ),
                inviter_account_id=creator_account_id
            )
        
        return care_circle
    
    def get_care_circle(self, circle_id: str) -> Optional[CareCircle]:
        """Get a care circle by ID."""
        return self.db.query(CareCircle).filter(CareCircle.id == circle_id).first()
    
    def get_person_care_circles(self, person_id: str, active_only: bool = True) -> List[CareCircle]:
        """Get all care circles for a person."""
        query = self.db.query(CareCircle).filter(CareCircle.person_id == person_id)
        
        if active_only:
            query = query.filter(CareCircle.is_active == 1)
            
        return query.all()
    
    def update_care_circle(self, circle_id: str, circle_data: CareCircleUpdate) -> Optional[CareCircle]:
        """Update a care circle."""
        care_circle = self.get_care_circle(circle_id)
        if not care_circle:
            return None
            
        update_data = circle_data.dict(exclude_unset=True)
        for field, value in update_data.items():
            if hasattr(care_circle, field):
                if isinstance(value, bool):
                    setattr(care_circle, field, 1 if value else 0)
                else:
                    setattr(care_circle, field, value)
        
        care_circle.updated_at = utc_now()
        self.db.commit()
        self.db.refresh(care_circle)
        
        return care_circle
    
    def deactivate_care_circle(self, circle_id: str) -> bool:
        """Deactivate a care circle (soft delete)."""
        care_circle = self.get_care_circle(circle_id)
        if not care_circle:
            return False
            
        care_circle.is_active = 0
        care_circle.updated_at = utc_now()
        self.db.commit()
        
        return True
    
    # Care Circle Membership operations
    def add_member_to_circle(self, care_circle_id: str, membership_data: CareCircleMembershipCreate, inviter_account_id: str) -> CareCircleMembership:
        """Add a member to a care circle."""
        # Verify the care circle exists
        care_circle = self.get_care_circle(care_circle_id)
        if not care_circle:
            raise ValueError(f"Care circle with ID {care_circle_id} not found")
        
        # Verify that either person_id or account_id is provided (but not both necessarily)
        if not membership_data.member_person_id and not membership_data.member_account_id:
            raise ValueError("Either member_person_id or member_account_id must be specified")
        
        # Verify the person exists if person_id is provided
        if membership_data.member_person_id:
            person = self.db.query(Person).filter(Person.id == membership_data.member_person_id).first()
            if not person:
                raise ValueError(f"Person with ID {membership_data.member_person_id} not found")
        
        # Verify the account exists if account_id is provided
        if membership_data.member_account_id:
            account = self.db.query(Account).filter(Account.id == membership_data.member_account_id).first()
            if not account:
                raise ValueError(f"Account with ID {membership_data.member_account_id} not found")
        
        # Verify the inviter account exists
        inviter = self.db.query(Account).filter(Account.id == inviter_account_id).first()
        if not inviter:
            raise ValueError(f"Inviter account with ID {inviter_account_id} not found")
        
        # Check if membership already exists
        existing_membership = self.db.query(CareCircleMembership).filter(
            and_(
                CareCircleMembership.care_circle_id == care_circle_id,
                or_(
                    CareCircleMembership.member_person_id == membership_data.member_person_id,
                    CareCircleMembership.member_account_id == membership_data.member_account_id
                ),
                CareCircleMembership.is_active == 1
            )
        ).first()
        
        if existing_membership:
            raise ValueError("Member is already part of this care circle")
        
        # Create the membership
        membership = CareCircleMembership(
            id=str(uuid4()),
            care_circle_id=care_circle_id,
            member_person_id=membership_data.member_person_id,
            member_account_id=membership_data.member_account_id,
            role=membership_data.role.value,
            role_description=membership_data.role_description,
            access_level=membership_data.access_level.value,
            can_view_information=1 if membership_data.can_view_information else 0,
            can_contribute_information=1 if membership_data.can_contribute_information else 0,
            can_receive_notifications=1 if membership_data.can_receive_notifications else 0,
            can_participate_in_decisions=1 if membership_data.can_participate_in_decisions else 0,
            relationship_to_person=membership_data.relationship_to_person,
            contact_information=membership_data.contact_information,
            invitation_status=CareCircleInvitationStatus.PENDING.value,
            invited_by_account_id=inviter_account_id,
            invited_at=utc_now(),
            created_by_account_id=inviter_account_id
        )
        
        self.db.add(membership)
        self.db.commit()
        self.db.refresh(membership)
        
        # Send invitation notification
        self._send_invitation_notification(membership, inviter)
        
        return membership
    
    def get_care_circle_memberships(self, circle_id: str, active_only: bool = True) -> List[CareCircleMembership]:
        """Get all memberships for a care circle."""
        query = self.db.query(CareCircleMembership).filter(CareCircleMembership.care_circle_id == circle_id)
        
        if active_only:
            query = query.filter(CareCircleMembership.is_active == 1)
            
        return query.all()
    
    def get_person_memberships(self, person_id: str, active_only: bool = True) -> List[CareCircleMembership]:
        """Get all care circle memberships for a person (as member_person)."""
        query = self.db.query(CareCircleMembership).filter(
            and_(
                CareCircleMembership.member_person_id == person_id,
                CareCircleMembership.is_active == 1 if active_only else True
            )
        )
        
        return query.all()
    
    def get_account_memberships(self, account_id: str, active_only: bool = True) -> List[CareCircleMembership]:
        """Get all care circle memberships for an account (as member_account)."""
        query = self.db.query(CareCircleMembership).filter(
            and_(
                CareCircleMembership.member_account_id == account_id,
                CareCircleMembership.is_active == 1 if active_only else True
            )
        )
        
        return query.all()
    
    def update_membership(self, membership_id: str, membership_data: CareCircleMembershipUpdate) -> Optional[CareCircleMembership]:
        """Update a care circle membership."""
        membership = self.db.query(CareCircleMembership).filter(CareCircleMembership.id == membership_id).first()
        if not membership:
            return None
            
        update_data = membership_data.dict(exclude_unset=True)
        for field, value in update_data.items():
            if hasattr(membership, field):
                if isinstance(value, bool):
                    setattr(membership, field, 1 if value else 0)
                elif field in ['role', 'access_level'] and isinstance(value, Enum):
                    setattr(membership, field, value.value)
                else:
                    setattr(membership, field, value)
        
        membership.updated_at = utc_now()
        self.db.commit()
        self.db.refresh(membership)
        
        return membership
    
    def remove_member_from_circle(self, membership_id: str) -> bool:
        """Remove a member from a care circle (soft delete)."""
        membership = self.db.query(CareCircleMembership).filter(CareCircleMembership.id == membership_id).first()
        if not membership:
            return False
            
        membership.is_active = 0
        membership.left_at = utc_now()
        membership.updated_at = utc_now()
        self.db.commit()
        
        return True
    
    def respond_to_invitation(self, membership_id: str, accept: bool, responder_account_id: str) -> Optional[CareCircleMembership]:
        """Respond to a care circle invitation."""
        membership = self.db.query(CareCircleMembership).filter(CareCircleMembership.id == membership_id).first()
        if not membership:
            return None
            
        # Verify the responder is the one who was invited
        if membership.invited_by_account_id != responder_account_id:
            # Also check if they're trying to respond via their person/account
            is_member_person = membership.member_person_id and \
                              self.db.query(Person).filter(
                                  Person.id == membership.member_person_id,
                                  Person.account_id == responder_account_id
                              ).first() is not None
            is_member_account = membership.member_account_id == responder_account_id
            
            if not (is_member_person or is_member_account):
                return None  # Not authorized to respond
        
        # Update the invitation status
        membership.invitation_status = (
            CareCircleInvitationStatus.ACCEPTED.value if accept 
            else CareCircleInvitationStatus.DECLINED.value
        )
        membership.responded_at = utc_now()
        membership.updated_at = utc_now()
        
        # If accepted, activate the membership
        if accept:
            membership.is_active = 1
            
            # Send welcome notification
            self._send_welcome_notification(membership)
        else:
            # If declined, we might want to leave it inactive or remove it entirely
            # For now, we'll leave it as declined but inactive
            membership.is_active = 0
        
        self.db.commit()
        self.db.refresh(membership)
        
        return membership
    
    # Helper methods for notifications
    def _send_invitation_notification(self, membership: CareCircleMembership, inviter: Account):
        """Send an invitation notification."""
        try:
            notification_service = create_notification_service(self.db)
            
            # Determine member name for the notification
            member_name = "Unknown"
            if membership.member_person:
                member_name = membership.member_person.name
            elif membership.member_account:
                member_name = membership.member_account.email or membership.member_account.full_name or "Unknown"
            
            notification = NotificationCreate(
                recipient_account_id=inviter.id,
                notification_type=NotificationType.INFO,
                title="Care Circle Invitation Sent",
                message=f"You've invited {member_name} to join the care circle.",
                related_entity_type="care_circle_membership",
                related_entity_id=membership.id
            )
            
            notification_service.create_notification(notification)
        except Exception:
            # Don't let notification failures break the main functionality
            pass
    
    def _send_welcome_notification(self, membership: CareCircleMembership):
        """Send a welcome notification when someone joins a care circle."""
        try:
            notification_service = create_notification_service(self.db)
            
            # Determine member name for the notification
            member_name = "Unknown"
            if membership.member_person:
                member_name = membership.member_person.name
            elif membership.member_account:
                member_name = membership.member_account.email or membership.member_account.full_name or "Unknown"
            
            # Determine circle name
            circle_name = "Unknown Circle"
            if membership.care_circle:
                circle_name = membership.care_circle.name
            
            notification = NotificationCreate(
                recipient_account_id=membership.member_account_id or \
                               (membership.member_person.account_id if membership.member_person else None),
                notification_type=NotificationType.INFO,
                title="Welcome to the Care Circle",
                message=f"You've been added to the '{circle_name}' care circle as a {membership.role.replace('_', ' ')}.",
                related_entity_type="care_circle",
                related_entity_id=membership.care_circle_id
            )
            
            notification_service.create_notification(notification)
        except Exception:
            # Don't let notification failures break the main functionality
            pass
    
    # Utility methods
    def get_accessible_information_for_member(self, person_id: str, member_account_id: str) -> Dict[str, Any]:
        """Get what information a member can access in a person's care record."""
        # Get all active memberships for this account in circles related to this person
        memberships = self.db.query(CareCircleMembership).join(CareCircle).filter(
            and_(
                CareCircleMembership.member_account_id == member_account_id,
                CareCircleMembership.is_active == 1,
                CareCircle.person_id == person_id,
                CareCircle.is_active == 1,
                CareCircleMembership.can_view_information == 1
            )
        ).all()
        
        if not memberships:
            return {"can_access": False, "reason": "No access permissions granted"}
        
        # Determine the highest access level
        access_levels = [m.access_level for m in memberships]
        # In a real implementation, we'd have a proper hierarchy
        # For now, we'll just check if any membership has full access
        has_full_access = any(level == "full" or level == "administrative" for level in access_levels)
        has_standard_access = any(level in ["standard", "full", "administrative"] for level in access_levels)
        
        return {
            "can_access": True,
            "access_level": "administrative" if has_full_access else "standard" if has_standard_access else "limited",
            "membership_count": len(memberships),
            "memberships": [
                {
                    "role": m.role,
                    "access_level": m.access_level,
                    "can_contribute_information": bool(m.can_contribute_information),
                    "can_receive_notifications": bool(m.can_receive_notifications),
                    "can_participate_in_decisions": bool(m.can_participate_in_decisions)
                }
                for m in memberships
            ]
        }