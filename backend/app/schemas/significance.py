from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional, List, Any, Dict
from app.core.change_enums import SignificanceVerdict, EvidenceStatus


class AssessmentDimensionInfo(BaseModel):
    name: str
    score: Optional[float] = None
    label: Optional[str] = None
    evidence_ids: Optional[List[str]] = None
    explanation: Optional[str] = None


class SignificanceAssessmentBase(BaseModel):
    person_id: str
    subject_type: str
    subject_id: str
    target_type: str = "change"
    target_id: Optional[str] = None
    baseline_id: Optional[str] = None
    verdict: SignificanceVerdict = SignificanceVerdict.INSUFFICIENT_EVIDENCE
    evidence_status: EvidenceStatus = EvidenceStatus.UNKNOWN
    significance_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    dimensions: List[Dict[str, Any]] = Field(default_factory=list)
    possible_context: Optional[List[str]] = None
    reporters: Optional[List[str]] = None
    attention_candidate: bool = False
    follow_up_candidate: bool = False
    explanation: Optional[str] = None
    evidence_ids: List[str] = Field(default_factory=list)


class SignificanceAssessmentCreate(SignificanceAssessmentBase):
    pass


class SignificanceAssessment(SignificanceAssessmentBase):
    id: str
    created_at: datetime
    updated_at: Optional[datetime] = None
    superseded_by_assessment_id: Optional[str] = None
    is_active: bool = True
    version: int = 1

    class Config:
        from_attributes = True
