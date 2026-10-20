"""Person Availability models for SolenOS.

Implements availability as a first-class continuity attribute that tracks
whether a person can currently carry out their responsibilities.
Separate from person identity, relationship, role, and responsibility.
"""

from sqlalchemy import Column, String, DateTime, Text, ForeignKey, Integer, Enum as SQLEnum, Index, JSON, Boolean
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
import uuid
from app.core.database import Base
from app.core.types import JSONList, JSONDict
from app.models.identity import Person, Account
from app.core.caregiver_enums import CaregiverAvailabilityStatus


class PersonAvailability(Base):
    """Tracks a person's availability to carry out responsibilities.
    
    This is separate from:
    - Person identity (who they are)
    - Relationship (how they're connected to care recipient)
    - Role (their function within the care system)
    - Responsibility (what they're accountable for)
    
    Availability is a first-class attribute that should be explicitly stated
    and not inferred from inactivity or lack of response.
    """
    __tablename__ = "person_availabilities"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    
    # Availability status
    availability_status = Column(SQLEnum(CaregiverAvailabilityStatus), nullable=False)  # available, unavailable, limited, unknown
    
    # Details about availability
    availability_description = Column(Text, nullable=True)  # Free-form description of availability
    
    # Temporal aspects
    effective_start = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    effective_end = Column(DateTime(timezone=True), nullable=True)  # NULL means ongoing
    
    # Specific availability patterns
    is_recurring = Column(Integer, nullable=False, default=0)  # 0 = false, 1 = true
    recurrence_pattern = Column(String, nullable=True)  # e.g., "weekdays 9am-5pm", "every Monday"
    timezone = Column(String, nullable=False, default="UTC")
    
    # Communication preferences when available
    preferred_contact_method = Column(String, nullable=True)  # phone, email, text, etc.
    contact_information = Column(JSONDict, nullable=True)  # Structured contact info
    
    # Source and verification
    availability_source = Column(String, nullable=True)  # How this availability was established
    verified_by_account_id = Column(String, ForeignKey("accounts.id"), nullable=True)
    verified_at = Column(DateTime(timezone=True), nullable=True)
    
    # Lifecycle
    is_active = Column(Integer, nullable=False, default=1)  # 0 = false, 1 = true
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # Relationships
    person = relationship("Person", foreign_keys=[person_id])
    verified_by = relationship("Account", foreign_keys=[verified_by_account_id])
    
    __table_args__ = (
        Index("ix_person_availabilities_person_active", "person_id", "is_active"),
        Index("ix_person_availabilities_status", "availability_status"),
        Index("ix_person_availabilities_effective", "effective_start", "effective_end"),
    )