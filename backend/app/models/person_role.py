"""Person Role models for SolenOS.

Implements the role layer that defines a person's function within the care system,
separate from their identity, relationship, and responsibility.
"""

from sqlalchemy import Column, String, DateTime, Text, ForeignKey, Integer, Enum as SQLEnum, Index
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
import uuid
from app.core.database import Base
from app.core.types import JSONList, JSONDict
from app.models.identity import Person
from app.models.caregiver import Caregiver


class PersonRole(Base):
    """Defines a person's function within the care system.
    
    This is separate from:
    - Person identity (who they are)
    - Relationship (how they're connected to care recipient)
    - Responsibility (what they're accountable for)
    - Availability (their availability)
    
    A person can have multiple roles (e.g., both "family caregiver" and "medication manager").
    Role does not automatically establish clinical authority or responsibility.
    """
    __tablename__ = "person_roles"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    
    # Role definition
    role_type = Column(String, nullable=False)  # e.g., "family caregiver", "nurse", "PT", "OT", "physician", "pharmacist"
    custom_role_name = Column(String, nullable=True)  # For roles not in standard list
    role_description = Column(Text, nullable=True)
    
    # Context
    is_primary = Column(Integer, nullable=False, default=0)  # Whether this is the primary role
    role_source = Column(String, nullable=True)  # How this role was established (self-reported, documented, etc.)
    
    # Authorization scope (what this role is authorized to do)
    permissions = Column(JSONList, nullable=True)  # List of permission strings
    restrictions = Column(JSONList, nullable=True)  # List of restriction strings
    
    # Lifecycle
    effective_start = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    effective_end = Column(DateTime(timezone=True), nullable=True)
    is_active = Column(Integer, nullable=False, default=1)  # 0 = false, 1 = true
    
    # Provenance
    created_by_account_id = Column(String, ForeignKey("accounts.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # Relationships
    person = relationship("Person", foreign_keys=[person_id])
    created_by = relationship("Account", foreign_keys=[created_by_account_id])
    
    __table_args__ = (
        Index("ix_person_roles_person_active", "person_id", "is_active"),
        Index("ix_person_roles_type", "role_type"),
    )