"""Person Relationship models for SolenOS.

Implements the relationship layer that defines how a person is connected
to the care recipient, separate from their identity, role, and responsibility.
"""

from sqlalchemy import Column, String, DateTime, Text, ForeignKey, Integer, Enum as SQLEnum, Index
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
import uuid
from app.core.database import Base
from app.core.types import JSONList, JSONDict
from app.models.identity import Person


class PersonRelationship(Base):
    """Defines how a person is connected to the care recipient.
    
    This is separate from:
    - Person identity (who they are)
    - Role (their function within the care system)
    - Responsibility (what they're accountable for)
    - Availability (their availability)
    """
    __tablename__ = "person_relationships"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    
    # Relationship to care recipient
    relationship_type = Column(String, nullable=False)  # e.g., "daughter", "spouse", "friend", "neighbor", "professional"
    relationship_description = Column(Text, nullable=True)  # Free-form description if needed
    
    # Context
    is_primary = Column(Integer, nullable=False, default=0)  # 0 = false, 1 = true - whether this is the primary relationship
    relationship_source = Column(String, nullable=True)  # How this relationship was established
    
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
        Index("ix_person_relationships_person_active", "person_id", "is_active"),
        Index("ix_person_relationships_type", "relationship_type"),
    )