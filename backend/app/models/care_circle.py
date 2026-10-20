"""Care Circle models for SolenOS.

Manages family and support networks around a person receiving care.
"""

from sqlalchemy import Column, String, DateTime, Text, Boolean, ForeignKey, Index, Integer
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
import uuid
from app.core.database import Base
from app.core.types import JSONList, JSONDict
from app.models.identity import Person, Account


class CareCircle(Base):
    """A care circle representing family and support network around a person.
    
    A care circle is a group of people (family, friends, professionals, 
    community members) who provide support to a person receiving care.
    """
    __tablename__ = "care_circles"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)  # Person the circle is for
    name = Column(String, nullable=False)  # Name of the care circle (e.g., "Family Circle", "Support Network")
    description = Column(Text, nullable=True)  # Description of the care circle's purpose
    is_active = Column(Integer, nullable=False, default=1)  # 0 = false, 1 = true
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # Relationships
    person = relationship("Person", foreign_keys=[person_id])
    memberships = relationship("CareCircleMembership", back_populates="care_circle", cascade="all, delete-orphan")
    
    __table_args__ = (
        Index("ix_care_circles_person_active", "person_id", "is_active"),
        Index("ix_care_circles_name", "name"),
    )


class CareCircleMembership(Base):
    """Membership of an entity (person or account) in a care circle.
    
    This defines who is part of the care circle and their role within it.
    """
    __tablename__ = "care_circle_memberships"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    care_circle_id = Column(String, ForeignKey("care_circles.id"), nullable=False, index=True)
    
    # The member can be either a person (for family/friends) or an account (for professionals/system users)
    member_person_id = Column(String, ForeignKey("persons.id"), nullable=True)  # Person who is a member
    member_account_id = Column(String, ForeignKey("accounts.id"), nullable=True)  # Account who is a member
    
    # Role within the care circle
    role = Column(String, nullable=False)  # e.g., "primary_family", "secondary_family", "friend", "neighbor", "volunteer", "professional_caregiver", "care_coordinator"
    role_description = Column(Text, nullable=True)  # Description of the role and responsibilities
    
    # Permissions and access level
    access_level = Column(String, nullable=False, default="limited")  # e.g., "none", "limited", "standard", "full", "administrative"
    can_view_information = Column(Integer, nullable=False, default=0)  # Can view shared information
    can_contribute_information = Column(Integer, nullable=False, default=0)  # Can contribute information to the circle
    can_receive_notifications = Column(Integer, nullable=False, default=0)  # Can receive notifications
    can_participate_in_decisions = Column(Integer, nullable=False, default=0)  # Can participate in care decisions
    
    # Relationship status and context
    relationship_to_person = Column(String, nullable=True)  # How the member relates to the person (e.g., "daughter", "friend", "neighbor", "home health aide")
    contact_information = Column(JSONDict, nullable=True)  # Contact info for the member
    
    # Temporal aspects
    joined_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    left_at = Column(DateTime(timezone=True), nullable=True)  # When they left the circle (null = still active)
    is_active = Column(Integer, nullable=False, default=1)  # 0 = false, 1 = true
    
    # Invitations and verification
    invitation_status = Column(String, nullable=False, default="pending")  # pending, accepted, declined, expired
    invited_by_account_id = Column(String, ForeignKey("accounts.id"), nullable=True)  # Who invited them
    invited_at = Column(DateTime(timezone=True), nullable=True)
    responded_at = Column(DateTime(timezone=True), nullable=True)
    
    # Relationships
    care_circle = relationship("CareCircle", back_populates="memberships")
    member_person = relationship("Person", foreign_keys=[member_person_id])
    member_account = relationship("Account", foreign_keys=[member_account_id])
    invited_by = relationship("Account", foreign_keys=[invited_by_account_id])
    
    __table_args__ = (
        Index("ix_care_circle_memberships_circle_active", "care_circle_id", "is_active"),
        Index("ix_care_circle_memberships_member_person", "member_person_id"),
        Index("ix_care_circle_memberships_member_account", "member_account_id"),
        Index("ix_care_circle_memberships_role", "role"),
        Index("ix_care_circle_memberships_access_level", "access_level"),
    )