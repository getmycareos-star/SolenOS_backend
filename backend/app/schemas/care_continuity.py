from datetime import datetime
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field, model_validator


class CareAssertionCreate(BaseModel):
    person_id: str
    subject_type: str = Field(min_length=1, max_length=80)
    subject_id: str = Field(min_length=1, max_length=160)
    field: str = Field(min_length=1, max_length=120)
    asserted_value: str = Field(min_length=1, max_length=4000)
    source_label: str = Field(min_length=1, max_length=160)
    source_text: Optional[str] = Field(default=None, max_length=12000)
    evidence_ids: List[str] = Field(default_factory=list, max_length=100)
    observed_at: Optional[datetime] = None
    review_after_days: int = Field(ge=1, le=3650)
    created_by_caregiver_id: Optional[str] = None


class CareAssertionResponse(BaseModel):
    id: str
    person_id: str
    subject_type: str
    subject_id: str
    field: str
    asserted_value: str
    source_label: str
    source_text: Optional[str] = None
    evidence_ids: List[str] = Field(default_factory=list)
    assertion_status: str
    observed_at: Optional[datetime] = None
    review_after_days: int
    created_by_caregiver_id: Optional[str] = None
    recorded_at: datetime

    class Config:
        from_attributes = True


class CareStateRevisionResponse(BaseModel):
    id: str
    person_id: str
    target_type: str
    target_id: str
    revision_number: int
    change_kind: str
    before_state: Optional[Dict[str, Any]] = None
    after_state: Optional[Dict[str, Any]] = None
    evidence_ids: List[str] = Field(default_factory=list)
    changed_by_caregiver_id: Optional[str] = None
    changed_at: datetime


class FreshnessResponse(BaseModel):
    assertion_id: str
    field: str
    status: Literal["within_review_window", "needs_confirmation", "historical"]
    age_days: Optional[int]
    review_after_days: int
    assertion_status: str
    explanation: str


class NextQuestionResponse(BaseModel):
    question: str
    subject_type: str
    subject_id: str
    priority: Literal["critical", "high", "medium", "low", "informational"]
    reason: str
    evidence_ids: List[str] = Field(default_factory=list)
    source_kind: str
    source_id: str
    persisted_question_id: Optional[str] = None


class CareActionOutcomeCreate(BaseModel):
    person_id: str
    action_id: str = Field(min_length=1, max_length=200)
    action_type: str = Field(min_length=1, max_length=100)
    outcome_status: Literal["useful", "completed", "corrected", "unresolved", "not_useful", "not_done"]
    notes: Optional[str] = Field(default=None, max_length=8000)
    correction_text: Optional[str] = Field(default=None, max_length=8000)
    evidence_ids: List[str] = Field(default_factory=list, max_length=100)
    created_by_caregiver_id: Optional[str] = None

    @model_validator(mode="after")
    def correction_required_for_corrected(self):
        if self.outcome_status == "corrected" and not (self.correction_text or "").strip():
            raise ValueError("correction_text is required when outcome_status is corrected")
        return self


class CareActionOutcomeResponse(BaseModel):
    id: str
    person_id: str
    action_id: str
    action_type: str
    outcome_status: str
    notes: Optional[str] = None
    correction_text: Optional[str] = None
    evidence_ids: List[str] = Field(default_factory=list)
    created_by_caregiver_id: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class CareAssertionStatusUpdate(BaseModel):
    person_id: str
    assertion_status: Literal["reported", "review_required", "accepted", "rejected", "superseded"]
