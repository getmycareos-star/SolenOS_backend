from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field
from app.core.types import JSONDict

# Medication schemas for API requests and responses

class MedicationBase(BaseModel):
    medication_name: str
    normalized_name: Optional[str] = None
    formulation: Optional[str] = None
    strength: Optional[str] = None
    
    # Instructions
    dose_quantity: Optional[float] = None
    dose_unit: Optional[str] = None
    frequency: Optional[str] = None
    frequency_per_period: Optional[int] = None
    frequency_period_unit: Optional[str] = None
    timing: Optional[str] = None
    route: Optional[str] = None
    with_food_instruction: Optional[str] = None
    administration_instructions: Optional[str] = None
    special_instructions: Optional[str] = None
    duration: Optional[str] = None
    
    # Refill information
    refill_status: Optional[str] = None
    refill_quantity: Optional[int] = None
    refill_date: Optional[datetime] = None
    pharmacy_name: Optional[str] = None
    pharmacy_phone: Optional[str] = None
    
    # Source information
    source_type: str
    source_description: Optional[str] = None
    source_document_id: Optional[str] = None
    source_date: Optional[datetime] = None
    
    # Clinical context
    purpose_indication: Optional[str] = None
    related_decision_id: Optional[str] = None
    related_appointment_id: Optional[str] = None
    related_hospitalization_id: Optional[str] = None
    related_professional_response_id: Optional[str] = None
    
    # Lifecycle
    lifecycle_state: str
    is_active: Optional[bool] = None
    
    # Continuity
    previous_version_id: Optional[str] = None
    supersedes_id: Optional[str] = None
    superseded_by_id: Optional[str] = None
    version_number: Optional[int] = None
    
    # Evidence and verification
    evidence_id: Optional[str] = None
    documented_date: Optional[datetime] = None
    effective_date: Optional[datetime] = None
    verification_status: str
    verified_by_account_id: Optional[str] = None
    verified_at: Optional[datetime] = None
    verification_note: Optional[str] = None
    
    # Change tracking
    change_type: Optional[str] = None
    change_description: Optional[str] = None
    change_source: Optional[str] = None
    change_date: Optional[datetime] = None
    change_evidence_id: Optional[str] = None
    
    # Open Loop integration
    open_loop_id: Optional[str] = None
    
    # Audit trail
    created_by_account_id: Optional[str] = None
    updated_by_account_id: Optional[str] = None


class MedicationCreate(MedicationBase):
    person_id: str


class MedicationUpdate(BaseModel):
    # All fields optional for partial updates
    medication_name: Optional[str] = None
    normalized_name: Optional[str] = None
    formulation: Optional[str] = None
    strength: Optional[str] = None
    
    # Instructions
    dose_quantity: Optional[float] = None
    dose_unit: Optional[str] = None
    frequency: Optional[str] = None
    frequency_per_period: Optional[int] = None
    frequency_period_unit: Optional[str] = None
    timing: Optional[str] = None
    route: Optional[str] = None
    with_food_instruction: Optional[str] = None
    administration_instructions: Optional[str] = None
    special_instructions: Optional[str] = None
    duration: Optional[str] = None
    
    # Refill information
    refill_status: Optional[str] = None
    refill_quantity: Optional[int] = None
    refill_date: Optional[datetime] = None
    pharmacy_name: Optional[str] = None
    pharmacy_phone: Optional[str] = None
    
    # Source information
    source_type: Optional[str] = None
    source_description: Optional[str] = None
    source_document_id: Optional[str] = None
    source_date: Optional[datetime] = None
    
    # Clinical context
    purpose_indication: Optional[str] = None
    related_decision_id: Optional[str] = None
    related_appointment_id: Optional[str] = None
    related_hospitalization_id: Optional[str] = None
    related_professional_response_id: Optional[str] = None
    
    # Lifecycle
    lifecycle_state: Optional[str] = None
    is_active: Optional[bool] = None
    
    # Continuity
    previous_version_id: Optional[str] = None
    supersedes_id: Optional[str] = None
    superseded_by_id: Optional[str] = None
    version_number: Optional[int] = None
    
    # Evidence and verification
    evidence_id: Optional[str] = None
    documented_date: Optional[datetime] = None
    effective_date: Optional[datetime] = None
    verification_status: Optional[str] = None
    verified_by_account_id: Optional[str] = None
    verified_at: Optional[datetime] = None
    verification_note: Optional[str] = None
    
    # Change tracking
    change_type: Optional[str] = None
    change_description: Optional[str] = None
    change_source: Optional[str] = None
    change_date: Optional[datetime] = None
    change_evidence_id: Optional[str] = None
    
    # Open Loop integration
    open_loop_id: Optional[str] = None
    
    # Audit trail
    created_by_account_id: Optional[str] = None
    updated_by_account_id: Optional[str] = None


class MedicationResponse(MedicationBase):
    id: str
    person_id: str
    created_at: datetime
    updated_at: datetime
    
    class Config:
        from_attributes = True


class MedicationListResponse(BaseModel):
    medications: List[MedicationResponse]
    total: int
    page: int
    size: int


# Medication reconciliation schemas

class MedicationReconciliationItem(BaseModel):
    medication_name: str
    formulation: Optional[str] = None
    strength: Optional[str] = None
    
    # Instructions
    dose_quantity: Optional[float] = None
    dose_unit: Optional[str] = None
    frequency: Optional[str] = None
    timing: Optional[str] = None
    route: Optional[str] = None
    with_food_instruction: Optional[str] = None
    administration_instructions: Optional[str] = None
    
    # Refill information
    refill_status: Optional[str] = None
    refill_quantity: Optional[int] = None
    
    # Source
    source_type: str
    source_description: Optional[str] = None
    source_date: Optional[datetime] = None
    
    # Status
    verification_status: str
    lifecycle_state: str
    is_active: bool


class MedicationReconciliationComparison(BaseModel):
    medication_name: str
    formulation: Optional[str] = None
    strength: Optional[str] = None
    
    # Side A (e.g., pre-hospital)
    side_a_dose_quantity: Optional[float] = None
    side_a_dose_unit: Optional[str] = None
    side_a_frequency: Optional[str] = None
    side_a_timing: Optional[str] = None
    side_a_route: Optional[str] = None
    side_a_with_food_instruction: Optional[str] = None
    side_a_administration_instructions: Optional[str] = None
    side_a_refill_status: Optional[str] = None
    side_a_refill_quantity: Optional[int] = None
    side_a_source_type: Optional[str] = None
    side_a_source_description: Optional[str] = None
    side_a_source_date: Optional[datetime] = None
    side_a_verification_status: Optional[str] = None
    side_a_lifecycle_state: Optional[str] = None
    side_a_is_active: Optional[bool] = None
    
    # Side B (e.g., post-hospital/discharge)
    side_b_dose_quantity: Optional[float] = None
    side_b_dose_unit: Optional[str] = None
    side_b_frequency: Optional[str] = None
    side_b_timing: Optional[str] = None
    side_b_route: Optional[str] = None
    side_b_with_food_instruction: Optional[str] = None
    side_b_administration_instructions: Optional[str] = None
    side_b_refill_status: Optional[str] = None
    side_b_refill_quantity: Optional[int] = None
    side_b_source_type: Optional[str] = None
    side_b_source_description: Optional[str] = None
    side_b_source_date: Optional[datetime] = None
    side_b_verification_status: Optional[str] = None
    side_b_lifecycle_state: Optional[str] = None
    side_b_is_active: Optional[bool] = None
    
    # Comparison result
    comparison_state: str  # UNCHANGED, NEW, REMOVED, DOSE_CHANGED, FREQUENCY_CHANGED, etc.
    differences: List[str] = []
    requires_confirmation: bool = False
    confirmation_question: Optional[str] = None
    suggested_confirmation_channel: Optional[str] = None


class MedicationReconciliationResult(BaseModel):
    person_id: str
    comparison_type: str  # e.g., "pre_hospital_vs_post_hospital"
    comparison_date: datetime
    medications: List[MedicationReconciliationComparison]
    summary: dict  # Summary statistics