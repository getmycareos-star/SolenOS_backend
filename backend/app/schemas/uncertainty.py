from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional, List, Any, Dict
from app.core.uncertainty_enums import (
    EpistemicState,
    GapReason,
    Priority,
    GapLifecycleStatus,
    ResolutionMechanism,
)


class InformationGapBase(BaseModel):
    person_id: str
    subject_type: str
    subject_id: str
    field: str
    epistemic_state: EpistemicState = EpistemicState.UNKNOWN
    gap_reason: GapReason = GapReason.ABSENT
    priority: Priority = Priority.INFORMATIONAL
    lifecycle_status: GapLifecycleStatus = GapLifecycleStatus.OPEN
    description: Optional[str] = None
    expected_by: Optional[datetime] = None
    resolution_mechanism: Optional[ResolutionMechanism] = None
    resolution_date: Optional[datetime] = None
    resolution_notes: Optional[str] = None
    evidence_ids: Optional[List[str]] = None
    context: Optional[List[Dict[str, Any]]] = None
    created_by_caregiver_id: Optional[str] = None
    updated_by_caregiver_id: Optional[str] = None


class InformationGapCreate(InformationGapBase):
    pass


class InformationGapUpdate(BaseModel):
    lifecycle_status: Optional[GapLifecycleStatus] = None
    priority: Optional[Priority] = None
    resolution_mechanism: Optional[ResolutionMechanism] = None
    resolution_date: Optional[datetime] = None
    resolution_notes: Optional[str] = None
    evidence_ids: Optional[List[str]] = None
    context: Optional[List[Dict[str, Any]]] = None
    updated_by_caregiver_id: Optional[str] = None


class InformationGap(InformationGapBase):
    id: str
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class OpenQuestionBase(BaseModel):
    person_id: str
    question: str
    subject_type: str
    subject_id: str
    status: str = "open"
    priority: Priority = Priority.INFORMATIONAL
    evidence_ids: Optional[List[str]] = None
    context: Optional[List[Dict[str, Any]]] = None
    answer: Optional[str] = None
    answer_provenance: Optional[str] = None
    closed_reason: Optional[str] = None
    created_by_caregiver_id: Optional[str] = None


class OpenQuestionCreate(OpenQuestionBase):
    pass


class OpenQuestionUpdate(BaseModel):
    status: Optional[str] = None
    priority: Optional[Priority] = None
    answer: Optional[str] = None
    answer_provenance: Optional[str] = None
    closed_reason: Optional[str] = None
    evidence_ids: Optional[List[str]] = None
    context: Optional[List[Dict[str, Any]]] = None


class OpenQuestion(OpenQuestionBase):
    id: str
    asked_at: datetime
    answered_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class ContradictionBase(BaseModel):
    person_id: str
    subject_type: str
    subject_id: str
    field: str
    description: str
    resolution_status: str = "unresolved"
    resolution_notes: Optional[str] = None
    resolved_by_caregiver_id: Optional[str] = None
    evidence_ids: Optional[List[str]] = None


class ContradictionCreate(ContradictionBase):
    pass


class ContradictionUpdate(BaseModel):
    resolution_status: Optional[str] = None
    resolution_notes: Optional[str] = None
    resolved_by_caregiver_id: Optional[str] = None
    evidence_ids: Optional[List[str]] = None


class Contradiction(ContradictionBase):
    id: str
    created_at: datetime
    updated_at: Optional[datetime] = None
    resolved_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class UncertaintyAssessmentBase(BaseModel):
    person_id: str
    subject_type: str
    subject_id: str
    field: str
    epistemic_state: EpistemicState
    confidence: Optional[float] = Field(None, ge=0.0, le=1.0)
    known_value: Optional[str] = None
    gap_reason: Optional[GapReason] = None
    context: Optional[List[Dict[str, Any]]] = None
    evidence_ids: Optional[List[str]] = None
    temporal_precision: Optional[str] = None
    assessed_by_caregiver_id: Optional[str] = None
    superseded_by_assessment_id: Optional[str] = None
    is_active: bool = True


class UncertaintyAssessmentCreate(UncertaintyAssessmentBase):
    pass


class UncertaintyAssessmentUpdate(BaseModel):
    epistemic_state: Optional[EpistemicState] = None
    confidence: Optional[float] = Field(None, ge=0.0, le=1.0)
    known_value: Optional[str] = None
    gap_reason: Optional[GapReason] = None
    context: Optional[List[Dict[str, Any]]] = None
    evidence_ids: Optional[List[str]] = None
    temporal_precision: Optional[str] = None
    superseded_by_assessment_id: Optional[str] = None
    is_active: Optional[bool] = None


class UncertaintyAssessment(UncertaintyAssessmentBase):
    id: str
    assessed_at: datetime

    class Config:
        from_attributes = True


class EpistemicView(BaseModel):
    subject_type: str
    subject_id: str
    field: str
    epistemic_state: EpistemicState
    confidence: Optional[float] = None
    known_value: Optional[str] = None
    gap_reason: Optional[GapReason] = None
    context: Optional[List[Dict[str, Any]]] = None
    evidence_ids: Optional[List[str]] = None
    temporal_precision: Optional[str] = None
    contradictions: Optional[List[Dict[str, Any]]] = None
    open_questions: Optional[List[Dict[str, Any]]] = None
    gaps: Optional[List[Dict[str, Any]]] = None


class SubjectUncertaintySummary(BaseModel):
    person_id: str
    subject_type: str
    subject_id: str
    total_fields: int
    known_count: int
    partially_known_count: int
    ambiguous_count: int
    conflicting_count: int
    unknown_count: int
    not_documented_count: int
    not_assessed_count: int
    stale_count: int
    historical_count: int
    open_gaps_count: int
    open_questions_count: int
    contradictions_count: int
