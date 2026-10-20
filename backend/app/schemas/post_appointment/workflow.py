from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from datetime import datetime
import uuid


class PostAppointmentWorkflowRequest(BaseModel):
    appointment_id: str
    capture_immediately: bool = False


class PostAppointmentCaptureData(BaseModel):
    captured_by_caregiver_id: str
    capture_timestamp: datetime
    capture_source: str
    appointment_outcome: str
    appointment_outcome_confidence: Optional[float] = None
    medication_changes: List[Dict[str, Any]] = Field(default_factory=list)
    new_diagnosis: Optional[str] = None
    new_appointments: List[Dict[str, Any]] = Field(default_factory=list)
    new_tests_orders: List[Dict[str, Any]] = Field(default_factory=list)
    new_responsibilities: List[Dict[str, Any]] = Field(default_factory=list)
    # Optional fields for reconciliation
    unresolved_questions: Optional[List[str]] = None


class PostAppointmentWorkflowResponse(BaseModel):
    workflow_id: str
    appointment_id: str
    status: str
    created_at: datetime
    next_steps: List[str]


class PostAppointmentReconciliationResult(BaseModel):
    reconciliation_id: str
    unchanged_items: List[Any]
    updated_items: List[Any]
    superseded_items: List[Any]
    confirmed_items: List[Any]
    contradicted_items: List[Any]
    unresolved_items: List[Any]
    unknown_items: List[Any]


class PostAppointmentCareStateUpdate(BaseModel):
    care_state_version: int
    updated_fields: List[str]
    previous_state_summary: Dict[str, Any]
    new_state_summary: Dict[str, Any]
    evidence_ids: List[str]