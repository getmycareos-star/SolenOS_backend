from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional, List


class ActionBase(BaseModel):
    person_id: str
    source_evidence_id: str
    source_passage: str
    source_document_type: Optional[str] = None
    source_authority: str = "unknown"
    extraction_confidence: float = Field(ge=0.0, le=1.0, default=1.0)
    provenance_notes: Optional[str] = None

    actor_type: str = "unknown"
    actor_id: Optional[str] = None
    actor_label: Optional[str] = None

    subject_type: str = "patient"
    subject_id: Optional[str] = None
    subject_label: Optional[str] = None

    action_type: str = "other"
    action_object: Optional[str] = None
    normalized_action: str
    original_text: str

    modality: str = "informational"
    condition_text: Optional[str] = None
    trigger_text: Optional[str] = None

    deadline: Optional[datetime] = None
    start_time: Optional[datetime] = None
    duration: Optional[str] = None
    recurrence: Optional[str] = None
    reference_frame_id: Optional[str] = None

    prerequisite_action_ids: Optional[List[str]] = None
    depends_on_event_id: Optional[str] = None

    status: str = "detected"
    completion_evidence_ids: Optional[List[str]] = None
    completion_confidence: Optional[float] = Field(ge=0.0, le=1.0, default=None)
    superseded_by_action_id: Optional[str] = None
    cancelled_at: Optional[datetime] = None
    expired_at: Optional[datetime] = None

    is_explicit: bool = True
    is_medication_action: bool = False
    risk_tier: str = "moderate"
    requires_confirmation: bool = False
    has_condition: bool = False
    is_recurring: bool = False

    created_by_caregiver_id: str


class ActionCreate(ActionBase):
    pass


class Action(ActionBase):
    id: str
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class ActionCandidate(BaseModel):
    source_evidence_id: str
    source_passage: str
    original_text: str
    extraction_confidence: float = Field(ge=0.0, le=1.0)
    actor_type: str = "unknown"
    actor_label: Optional[str] = None
    action_type: str = "other"
    action_object: Optional[str] = None
    normalized_action: str
    modality: str = "informational"
    condition_text: Optional[str] = None
    trigger_text: Optional[str] = None
    deadline: Optional[datetime] = None
    start_time: Optional[datetime] = None
    duration: Optional[str] = None
    recurrence: Optional[str] = None
    is_explicit: bool = True
    is_medication_action: bool = False
    risk_tier: str = "moderate"
    requires_confirmation: bool = False
    has_condition: bool = False
    is_recurring: bool = False
    status: str = "detected"
    provenance_notes: Optional[str] = None


class ActionCompletionEvidence(BaseModel):
    evidence_ids: List[str]
    completion_confidence: float = Field(ge=0.0, le=1.0)
    notes: Optional[str] = None


class ActionUpdate(BaseModel):
    status: Optional[str] = None
    normalized_action: Optional[str] = None
    modality: Optional[str] = None
    deadline: Optional[datetime] = None
    actor_type: Optional[str] = None
    actor_label: Optional[str] = None
    completion_evidence_ids: Optional[List[str]] = None
    completion_confidence: Optional[float] = Field(ge=0.0, le=1.0, default=None)
    superseded_by_action_id: Optional[str] = None
    requires_confirmation: Optional[bool] = None
    provenance_notes: Optional[str] = None


class ActionDeduplicationResult(BaseModel):
    is_duplicate: bool
    existing_action_id: Optional[str] = None
    confidence: float = Field(ge=0.0, le=1.0)


class ActionSafetyCheck(BaseModel):
    is_safe_to_create: bool
    requires_confirmation: bool
    risk_tier: str
    warnings: List[str] = []
    blocked_reason: Optional[str] = None
