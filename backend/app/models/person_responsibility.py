"""Person Responsibility models for SolenOS.

Implements responsibility as a first-class continuity object that answers
"Who is responsible for what?" separate from person identity, relationship, and role.
"""

from sqlalchemy import Column, String, DateTime, Text, ForeignKey, Integer, Enum as SQLEnum, Index, JSON, Boolean
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
import uuid
from app.core.database import Base
from app.core.types import JSONList, JSONDict
from app.models.identity import Person, Account
from app.core.handoff_enums import ResponsibilityStatus, ResponsibilityType


class PersonResponsibility(Base):
    """A first-class continuity object representing responsibility.
    
    Answers: "Who is responsible for what?"
    
    This is separate from:
    - Person identity (who they are)
    - Relationship (how they're connected to care recipient)
    - Role (their function within the care system)
    - Availability (their availability)
    
    A responsibility can exist without an owner (unassigned responsibility).
    Responsibility establishes who owns continuity while a task represents
    a specific action or occurrence.
    """
    __tablename__ = "person_responsibilities"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)  # Who the responsibility is about (care recipient)
    
    # Responsibility definition
    responsibility_type = Column(SQLEnum(ResponsibilityType), nullable=False)  # Type of responsibility
    custom_responsibility_type = Column(String, nullable=True)  # For types not in enum
    title = Column(String, nullable=False)  # Short name of the responsibility
    description = Column(Text, nullable=True)  # Detailed description
    instructions = Column(Text, nullable=True)  # Specific instructions for fulfilling this responsibility
    
    # Ownership
    owner_account_id = Column(String, ForeignKey("accounts.id"), nullable=True)  # Who owns this responsibility (can be NULL for unassigned)
    owner_person_id = Column(String, ForeignKey("persons.id"), nullable=True)  # Alternative link to person who owns it
    
    # Context and scope
    scope = Column(Text, nullable=True)  # Scope or limitations of this responsibility
    permission_scope = Column(String, nullable=True)  # What the owner is authorized to do (read_only, read_and_act, etc.)
    
    # Temporal aspects
    effective_start = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    effective_end = Column(DateTime(timezone=True), nullable=True)
    expected_timing = Column(String, nullable=True)  # When this should be done (e.g., "daily", "weekly", "every Monday")
    frequency = Column(Integer, nullable=True)  # How often (if applicable)
    
    # Status
    status = Column(SQLEnum(ResponsibilityStatus), nullable=False, default=ResponsibilityStatus.PROPOSED)
    
    # Dependencies
    depends_on = Column(JSONList, nullable=True)  # List of other responsibility IDs or event IDs this depends on
    
    # Evidence and provenance
    source_evidence_ids = Column(JSONList, nullable=True)  # Evidence supporting this responsibility assignment
    provenance = Column(Text, nullable=True)  # How this responsibility was established
    
    # Completion and failure tracking
    completion_evidence_ids = Column(JSONList, nullable=True)  # Evidence of completion
    failure_reason = Column(Text, nullable=True)  # Reason for failure if applicable
    
    # Reassignment history (stored as JSON for flexibility)
    reassignment_history = Column(JSONList, nullable=True)  # History of responsibility transfers
    
    # Verification
    verification_status = Column(String, nullable=True)  # Verification state of this responsibility
    verified_by_account_id = Column(String, ForeignKey("accounts.id"), nullable=True)
    verified_at = Column(DateTime(timezone=True), nullable=True)
    
    # Lifecycle
    is_active = Column(Integer, nullable=False, default=1)  # 0 = false, 1 = true
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # Relationships
    person = relationship("Person", foreign_keys=[person_id])  # Person the responsibility is about
    owner_account = relationship("Account", foreign_keys=[owner_account_id])  # Account that owns the responsibility
    owner_person = relationship("Person", foreign_keys=[owner_person_id])  # Person that owns the responsibility (if different from cared-for person)
    verified_by = relationship("Account", foreign_keys=[verified_by_account_id])
    
    __table_args__ = (
        Index("ix_person_responsibilities_person_active", "person_id", "is_active"),
        Index("ix_person_responsibilities_owner", "owner_account_id"),
        Index("ix_person_responsibilities_status", "status"),
        Index("ix_person_responsibilities_type", "responsibility_type"),
        Index("ix_person_responsibilities_effective", "effective_start", "effective_end"),
    )