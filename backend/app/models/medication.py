from sqlalchemy import Column, String, DateTime, Date, Boolean, Text, Float, Integer, ForeignKey, JSON, Index, UniqueConstraint
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
import uuid
from app.core.database import Base
from app.core.types import JSONList, JSONDict
from app.models.identity import Person
from app.models.care_event import CareEvent as CareEvent
from app.models.evidence import Evidence as Evidence
from app.models.caregiver import Caregiver
from app.models.open_loop import OpenLoop


# Medication status enum values as defined in the specification
MEDICATION_LIFECYCLE_STATES = [
    'active', 'newly_prescribed', 'changed', 'held', 'discontinued', 
    'completed', 'historical', 'superseded', 'unknown'
]

VERIFICATION_STATES = [
    'DOCUMENTED', 'UNVERIFIED', 'VERIFIED', 'CONFLICTING', 'STALE', 
    'SUPERSEDED', 'UNKNOWN'
]

MEDICATION_CHANGE_TYPES = [
    'dose', 'frequency', 'timing', 'route', 'administration', 
    'start', 'hold', 'stop', 'restart', 'substitution', 'formulation'
]

REFILL_STATUS_STATES = [
    'available', 'low_stock', 'out_of_stock', 'pending_refill', 
    'refill_delayed', 'refill_denied', 'unknown'
]


class Medication(Base):
    """Canonical medication entity supporting medication continuity and reconciliation."""
    
    __tablename__ = "medications"

    # Primary identification
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    
    # Person receiving care
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    
    # Medication identity
    medication_name = Column(String, nullable=False, index=True)
    normalized_name = Column(String, nullable=True, index=True)  # Standardized medication name
    formulation = Column(String, nullable=True)  # e.g., "10mg tablet", "5mg/mL solution"
    strength = Column(String, nullable=True)  # Active ingredient strength
    
    # Instructions - structured where available
    dose_quantity = Column(Float, nullable=True)  # Numeric dose amount
    dose_unit = Column(String, nullable=True)    # e.g., "mg", "mL", "tablet"
    frequency = Column(String, nullable=True)    # e.g., "twice daily", "every 8 hours"
    frequency_per_period = Column(Integer, nullable=True)  # e.g., 2 for twice daily
    frequency_period_unit = Column(String, nullable=True)  # e.g., "day", "hour"
    timing = Column(String, nullable=True)       # e.g., "morning", "evening", "08:00, 20:00"
    route = Column(String, nullable=True)        # e.g., "oral", "topical", "inhaled"
    with_food_instruction = Column(String, nullable=True)  # e.g., "with food", "without food"
    administration_instructions = Column(Text, nullable=True)  # e.g., "swallow whole", "dissolve"
    special_instructions = Column(Text, nullable=True)
    duration = Column(String, nullable=True)     # If explicitly documented
    
    # Refill information
    refill_status = Column(String, nullable=True)  # From REFILL_STATUS_STATES
    refill_quantity = Column(Integer, nullable=True)  # Number of refills remaining
    refill_date = Column(DateTime(timezone=True), nullable=True)  # Date refill is available
    pharmacy_name = Column(String, nullable=True)  # Pharmacy providing medication
    pharmacy_phone = Column(String, nullable=True)  # Pharmacy contact
    
    # Source information
    source_type = Column(String, nullable=False)  # physician, nurse practitioner, etc.
    source_description = Column(Text, nullable=True)  # Free text description of source
    source_document_id = Column(String, ForeignKey("evidence.id"), nullable=True)  # Link to source evidence
    source_date = Column(DateTime(timezone=True), nullable=True)  # When source was created/documented
    
    # Clinical context
    purpose_indication = Column(Text, nullable=True)  # Only when explicitly documented
    related_decision_id = Column(String, ForeignKey("care_events.id"), nullable=True)  # Related care decision
    related_appointment_id = Column(String, ForeignKey("care_events.id"), nullable=True)  # Related appointment
    related_hospitalization_id = Column(String, ForeignKey("care_events.id"), nullable=True)  # Related hospitalization
    related_professional_response_id = Column(String, ForeignKey("care_events.id"), nullable=True)  # Professional response
    
    # Lifecycle management
    lifecycle_state = Column(String, nullable=False, index=True)  # From MEDICATION_LIFECYCLE_STATES
    is_active = Column(Boolean, nullable=False, default=True, index=True)  # Derived from lifecycle_state
    
    # Continuity and versioning
    previous_version_id = Column(String, ForeignKey("medications.id"), nullable=True, index=True)
    supersedes_id = Column(String, ForeignKey("medications.id"), nullable=True, index=True)  # What this medication replaces
    superseded_by_id = Column(String, ForeignKey("medications.id"), nullable=True, index=True)  # What replaces this medication
    version_number = Column(Integer, nullable=False, default=1)  # Version sequence
    
    # Evidence and verification
    evidence_id = Column(String, ForeignKey("evidence.id"), nullable=True)  # Supporting evidence
    documented_date = Column(DateTime(timezone=True), nullable=False, server_default=func.now())  # When documented in system
    effective_date = Column(DateTime(timezone=True), nullable=True)  # When medication instruction takes effect
    verification_status = Column(String, nullable=False, index=True)  # From VERIFICATION_STATES
    verified_by_account_id = Column(String, ForeignKey("accounts.id"), nullable=True)  # Who verified
    verified_at = Column(DateTime(timezone=True), nullable=True)  # When verified
    verification_note = Column(Text, nullable=True)  # Notes from verification
    
    # Change tracking - for medication changes as first-class events
    change_type = Column(String, nullable=True)  # From MEDICATION_CHANGE_TYPES
    change_description = Column(Text, nullable=True)  # Description of what changed
    change_source = Column(String, nullable=True)  # Source of the change
    change_date = Column(DateTime(timezone=True), nullable=True)  # When change occurred
    change_evidence_id = Column(String, ForeignKey("evidence.id"), nullable=True)  # Evidence for change
    
    # Open Loop integration - for items requiring confirmation
    open_loop_id = Column(String, ForeignKey("open_loops.id"), nullable=True, index=True)  # Linked open loop for confirmation needed
    
    # Audit trail
    created_by_account_id = Column(String, ForeignKey("accounts.id"), nullable=True)  # Who created this record
    updated_by_account_id = Column(String, ForeignKey("accounts.id"), nullable=True)  # Who last updated
    
    # Metadata
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # Relationships
    person = relationship("Person", foreign_keys=[person_id])
    source_evidence = relationship("Evidence", foreign_keys=[source_document_id])
    related_decision = relationship("CareEvent", foreign_keys=[related_decision_id])
    related_appointment = relationship("CareEvent", foreign_keys=[related_appointment_id])
    related_hospitalization = relationship("CareEvent", foreign_keys=[related_hospitalization_id])
    related_professional_response = relationship("CareEvent", foreign_keys=[related_professional_response_id])
    previous_version = relationship("Medication", foreign_keys=[previous_version_id], remote_side=[id])
    superseded_medication = relationship("Medication", foreign_keys=[supersedes_id], remote_side=[id])
    superseded_by_medication = relationship("Medication", foreign_keys=[superseded_by_id], remote_side=[id])
    change_evidence = relationship("Evidence", foreign_keys=[change_evidence_id])
    verifier = relationship("Account", foreign_keys=[verified_by_account_id])
    supporting_evidence = relationship("Evidence", foreign_keys=[evidence_id])
    open_loop = relationship("OpenLoop", foreign_keys=[open_loop_id])
    creator = relationship("Account", foreign_keys=[created_by_account_id])
    updater = relationship("Account", foreign_keys=[updated_by_account_id])

    # Indexes for common queries
    __table_args__ = (
        Index('ix_medications_person_active', 'person_id', 'is_active'),
        Index('ix_medications_person_lifecycle', 'person_id', 'lifecycle_state'),
        Index('ix_medications_verification', 'verification_status'),
        Index('ix_medications_effective_date', 'effective_date'),
        Index('ix_medications_documentation', 'documented_date'),
        Index('ix_medications_source', 'source_type'),
        Index('ix_medications_open_loop', 'open_loop_id'),
        Index('ix_medications_refill_status', 'refill_status'),
    )
    
    def __repr__(self):
        return f"<Medication {self.medication_name} ({self.lifecycle_state})>"