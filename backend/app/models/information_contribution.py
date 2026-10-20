"""Information Contribution models for SolenOS.

Tracks who contributed what information to the care record.
Separate from person identity, relationship, role, responsibility, and availability.
"""

from sqlalchemy import Column, String, DateTime, Text, ForeignKey, Integer, Enum as SQLEnum, Index, JSON, Boolean
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
import uuid
from app.core.database import Base
from app.core.types import JSONList, JSONDict
from app.models.identity import Person, Account
from app.models.evidence import Evidence


class InformationContribution(Base):
    """Tracks who contributed what information to the care record.
    
    This is separate from:
    - Person identity (who they are)
    - Relationship (how they're connected to care recipient)
    - Role (their function within the care system)
    - Responsibility (what they're accountable for)
    - Availability (their availability)
    
    This establishes information provenance - who last contributed information
    about a specific aspect of care, which is different from who is responsible
    for acting on that information.
    """
    __tablename__ = "information_contributions"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)  # The person the information is about
    
    # Contributor information
    contributor_account_id = Column(String, ForeignKey("accounts.id"), nullable=False)  # Who contributed the information
    contributor_person_id = Column(String, ForeignKey("persons.id"), nullable=True)  # Alternative link to contributor as person
    
    # What was contributed
    contribution_type = Column(String, nullable=False)  # e.g., "observation", "measurement", "decision", "instruction"
    contribution_description = Column(Text, nullable=False)  # Description of what was contributed
    
    # Evidence linking
    evidence_ids = Column(JSONList, nullable=True)  # IDs of evidence records that document this contribution
    related_evidence_ids = Column(JSONList, nullable=True)  # Other evidence related to this contribution
    
    # Context
    contribution_context = Column(Text, nullable=True)  # Context in which the information was contributed
    contribution_source = Column(String, nullable=True)  # How the information was obtained (direct observation, report, etc.)
    
    # Temporal aspects
    contributed_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    effective_until = Column(DateTime(timezone=True), nullable=True)  # When this information is no longer considered current
    
    # Verification and validation
    verification_status = Column(String, nullable=True)  # Verification state of this information
    verified_by_account_id = Column(String, ForeignKey("accounts.id"), nullable=True)
    verified_at = Column(DateTime(timezone=True), nullable=True)
    
    # Lifecycle
    is_active = Column(Integer, nullable=False, default=1)  # 0 = false, 1 = true
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # Relationships
    person = relationship("Person", foreign_keys=[person_id])  # Person the information is about
    contributor_account = relationship("Account", foreign_keys=[contributor_account_id])  # Account that contributed
    contributor_person = relationship("Person", foreign_keys=[contributor_person_id])  # Person that contributed (if different)
    verified_by = relationship("Account", foreign_keys=[verified_by_account_id])
    # Note: We don't create relationships to Evidence here to avoid circular imports
    # Evidence relationships are handled in the Evidence model
    
    __table_args__ = (
        Index("ix_information_contributions_person_active", "person_id", "is_active"),
        Index("ix_information_contributions_contributor", "contributor_account_id"),
        Index("ix_information_contributions_contributed_at", "contributed_at"),
        Index("ix_information_contributions_type", "contribution_type"),
    )