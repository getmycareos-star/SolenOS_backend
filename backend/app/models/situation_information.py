"""
Situation Information Model for SolenOS.

Represents structured, verified information about a care recipient's status
at a point in time, organized by clinical/functional domains with clear
provenance, timing, and confidence levels.

This is distinct from:
- Situation: A summary/container of understanding about a care situation
- CareEvent: A specific care-relevant occurrence
- Evidence: Raw observations or source material
- CaregiverInput: Raw reports from caregivers

Instead, SituationInformation represents the synthesized, verified
understanding of a care recipient's status - what we actually know with
confidence about their condition, organized by domain.
"""

from sqlalchemy import Column, String, DateTime, Text, Boolean, Float, ForeignKey, Integer, Enum as SQLEnum, Index, UniqueConstraint, JSON
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
import uuid
from app.core.database import Base
from app.core.types import JSONList, JSONDict
from app.core.situation_information_enums import (
    InformationDomain,
    InformationStatus,
    InformationConfidenceLevel,
    InformationSourceType,
    InformationValidationStatus
)


class SituationInformation(Base):
    """Structured information about a care recipient's status at a point in time.
    
    Represents verified, organized knowledge about a care recipient's
    condition, organized by clinical/functional domains.
    """
    __tablename__ = "situation_information"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    
    # Information organization
    domain = Column(SQLEnum(InformationDomain), nullable=False, index=True)  # e.g., "mobility", "cognition", "medication"
    category = Column(String, nullable=True)  # Sub-category within domain
    title = Column(String, nullable=False)  # Brief description of what this information covers
    description = Column(Text, nullable=True)  # Detailed description
    
    # Information content (structured)
    information_value = Column(JSONDict, nullable=True)  # Structured data representing the information
    information_summary = Column(Text, nullable=True)  # Human-readable summary
    
    # Temporal aspects
    observed_period_start = Column(DateTime(timezone=True), nullable=True)  # Start of observation period
    observed_period_end = Column(DateTime(timezone=True), nullable=True)   # End of observation period
    recorded_at = Column(DateTime(timezone=True), server_default=func.now())  # When this information was recorded
    valid_until = Column(DateTime(timezone=True), nullable=True)  # When this information expires/should be reviewed
    
    # Source and attribution
    source_type = Column(SQLEnum(InformationSourceType), nullable=False)  # How this information was obtained
    source_description = Column(Text, nullable=True)  # Description of the source
    source_reference_id = Column(String, nullable=True)  # Reference to source evidence/event/caregiver
    source_confidence = Column(Float, nullable=True)  # Confidence in the source itself
    
    # Validation and confidence
    status = Column(SQLEnum(InformationStatus), nullable=False, default=InformationStatus.PENDING_REVIEW)
    confidence_level = Column(SQLEnum(InformationConfidenceLevel), nullable=False, default=InformationConfidenceLevel.LOW)
    validation_status = Column(SQLEnum(InformationValidationStatus), nullable=False, default=InformationValidationStatus.NOT_VALIDATED)
    validated_by = Column(String, ForeignKey("caregivers.id"), nullable=True)  # Who validated this information
    validated_at = Column(DateTime(timezone=True), nullable=True)
    validation_notes = Column(Text, nullable=True)
    
    # Quality and reliability
    information_quality = Column(Float, nullable=True)  # 0.0-1.0 quality score
    reliability_score = Column(Float, nullable=True)  # 0.0-1.0 reliability based on source consistency
    corroborating_sources = Column(JSONList, nullable=True)  # Other sources that support this information
    conflicting_sources = Column(JSONList, nullable=True)  # Sources that contradict this information
    
    # Relationship to situations (what situations this information contributes to)
    situation_ids = Column(JSONList, nullable=True)  # List of situation IDs this information contributes to
    contribution_type = Column(String, nullable=True)  # How this information contributes (e.g., "supporting", "contradicting", "contextual")
    
    # Authorization and privacy
    information_sensitivity = Column(String, nullable=True)  # Sensitivity level for access control
    privacy_restrictions = Column(JSONList, nullable=True)  # Specific privacy restrictions
    
    # Processing metadata
    processed_by = Column(String, ForeignKey("caregivers.id"), nullable=True)  # Who processed/compiled this information
    processing_notes = Column(Text, nullable=True)
    processing_version = Column(String, nullable=True)  # Version of processing algorithms used
    
    # Lifecycle
    is_active = Column(Boolean, nullable=False, default=True)
    superseded_by_information_id = Column(String, ForeignKey("situation_information.id"), nullable=True)
    superseded_at = Column(DateTime(timezone=True), nullable=True)
    supersession_reason = Column(Text, nullable=True)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # Relationships
    person = relationship("Person", foreign_keys=[person_id])
    validated_by_caregiver = relationship("Caregiver", foreign_keys=[validated_by])
    processed_by_caregiver = relationship("Caregiver", foreign_keys=[processed_by])
    superseded_by = relationship("SituationInformation", remote_side=[id], foreign_keys=[superseded_by_information_id])
    
    __table_args__ = (
        Index("ix_situation_information_person_domain", "person_id", "domain"),
        Index("ix_situation_information_status", "status"),
        Index("ix_situation_information_confidence", "confidence_level"),
        Index("ix_situation_information_active", "is_active"),
        UniqueConstraint("person_id", "domain", "title", "observed_period_start", name="uq_situation_information_unique"),
    )


class SituationInformationVersion(Base):
    """Immutable history of situation information changes."""
    __tablename__ = "situation_information_versions"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    situation_information_id = Column(String, ForeignKey("situation_information.id"), nullable=False, index=True)
    version = Column(Integer, nullable=False)

    # Snapshot of all significant fields
    person_id = Column(String, nullable=False)
    domain = Column(SQLEnum(InformationDomain), nullable=False)
    category = Column(String, nullable=True)
    title = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    information_value = Column(JSONDict, nullable=True)
    information_summary = Column(Text, nullable=True)
    
    observed_period_start = Column(DateTime(timezone=True), nullable=True)
    observed_period_end = Column(DateTime(timezone=True), nullable=True)
    recorded_at = Column(DateTime(timezone=True), nullable=False)
    valid_until = Column(DateTime(timezone=True), nullable=True)
    
    source_type = Column(SQLEnum(InformationSourceType), nullable=False)
    source_description = Column(Text, nullable=True)
    source_reference_id = Column(String, nullable=True)
    source_confidence = Column(Float, nullable=True)
    
    status = Column(SQLEnum(InformationStatus), nullable=False)
    confidence_level = Column(SQLEnum(InformationConfidenceLevel), nullable=False)
    validation_status = Column(SQLEnum(InformationValidationStatus), nullable=False)
    validated_by = Column(String, nullable=True)
    validated_at = Column(DateTime(timezone=True), nullable=True)
    validation_notes = Column(Text, nullable=True)
    
    information_quality = Column(Float, nullable=True)
    reliability_score = Column(Float, nullable=True)
    corroborating_sources = Column(JSONList, nullable=True)
    conflicting_sources = Column(JSONList, nullable=True)
    
    situation_ids = Column(JSONList, nullable=True)
    contribution_type = Column(String, nullable=True)
    
    information_sensitivity = Column(String, nullable=True)
    privacy_restrictions = Column(JSONList, nullable=True)
    
    processed_by = Column(String, nullable=True)
    processing_notes = Column(Text, nullable=True)
    processing_version = Column(String, nullable=True)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("situation_information_id", "version", name="uq_situation_information_version"),
        Index("ix_situation_information_version_info", "situation_information_id", "version"),
    )


# Update Situation model to add back-reference
# In app/models/change.py, Situation class should have:
# information_contributions = relationship("SituationInformation", foreign_keys="SituationInformation.situation_ids")