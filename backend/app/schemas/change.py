from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional, List, Any
from app.core.change_enums import (
    ChangeType,
    ChangeDirection,
    PatternType,
    PatternStrength,
    SituationState,
    SituationType,
    BaselineType,
    BaselineConfidence,
    SignificanceVerdict,
)


class BaselineBase(BaseModel):
    person_id: str
    subject_type: str
    subject_id: str
    baseline_type: BaselineType = BaselineType.PERSONAL_HISTORICAL
    window_start: datetime
    window_end: datetime
    event_count: int = 0
    frequency_per_period: Optional[float] = None
    state_value: Optional[str] = None
    confidence: BaselineConfidence = BaselineConfidence.MODERATE
    is_stable: bool = True
    observation_density: Optional[float] = None
    documentation_bias_flag: bool = False
    evidence_ids: Optional[List[str]] = None


class BaselineCreate(BaselineBase):
    pass


class Baseline(BaselineBase):
    id: str
    created_at: datetime
    updated_at: Optional[datetime] = None
    superseded_by_baseline_id: Optional[str] = None
    is_active: bool = True

    class Config:
        from_attributes = True


class ChangeBase(BaseModel):
    person_id: str
    subject_type: str
    subject_id: str
    change_type: ChangeType
    change_direction: ChangeDirection = ChangeDirection.UNKNOWN
    previous_state: Optional[str] = None
    current_state: Optional[str] = None
    magnitude: Optional[float] = None
    previous_count: Optional[int] = None
    current_count: Optional[int] = None
    baseline_id: Optional[str] = None
    comparison_window_start: Optional[datetime] = None
    comparison_window_end: Optional[datetime] = None
    persistence_days: Optional[int] = None
    is_persistent: Optional[bool] = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    evidence_ids: List[str]
    explanation: Optional[str] = None
    time_provenance: Optional[str] = None
    significance_assessment_id: Optional[str] = None
    significance_verdict: Optional[SignificanceVerdict] = None
    attention_candidate: bool = False
    follow_up_candidate: bool = False


class ChangeCreate(ChangeBase):
    pass


class Change(ChangeBase):
    id: str
    created_at: datetime
    updated_at: Optional[datetime] = None
    superseded_by_change_id: Optional[str] = None
    is_active: bool = True

    class Config:
        from_attributes = True


class PatternBase(BaseModel):
    person_id: str
    subject_type: str
    subject_id: str
    pattern_type: PatternType
    pattern_strength: PatternStrength = PatternStrength.EMERGING
    event_ids: List[str]
    change_ids: Optional[List[str]] = None
    temporal_start: datetime
    temporal_end: datetime
    frequency: Optional[float] = None
    rate_per_period: Optional[float] = None
    direction: Optional[ChangeDirection] = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    evidence_count: int = 0
    alternative_interpretations: Optional[List[dict[str, Any]]] = None


class PatternCreate(PatternBase):
    pass


class Pattern(PatternBase):
    id: str
    created_at: datetime
    updated_at: Optional[datetime] = None
    superseded_by_pattern_id: Optional[str] = None
    is_active: bool = True

    class Config:
        from_attributes = True


class SituationBase(BaseModel):
    person_id: str
    situation_type: SituationType = SituationType.UNKNOWN
    state: SituationState = SituationState.ACTIVE
    title: str
    description: Optional[str] = None
    signal_ids: List[str]
    signal_types: List[str]
    temporal_start: datetime
    temporal_end: datetime
    pattern_ids: Optional[List[str]] = None
    change_ids: Optional[List[str]] = None
    significance_assessment_id: Optional[str] = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    alternative_interpretations: Optional[List[dict[str, Any]]] = None
    context: Optional[dict[str, Any]] = None
    attention_candidate: bool = False
    follow_up_candidate: bool = False
    explanation: Optional[str] = None


class SituationCreate(SituationBase):
    pass


class Situation(SituationBase):
    id: str
    created_at: datetime
    updated_at: Optional[datetime] = None
    superseded_by_situation_id: Optional[str] = None
    is_active: bool = True

    class Config:
        from_attributes = True


class SituationResponse(BaseModel):
    ok: bool
    situation_id: str
    person_id: str
    title: str
    description: Optional[str] = None
    situation_type: SituationType
    state: SituationState
    confidence: float
    explanation: Optional[str] = None
    signals: List[dict[str, Any]]
    patterns: Optional[List[dict[str, Any]]] = None
    changes: Optional[List[dict[str, Any]]] = None
    alternative_interpretations: Optional[List[dict[str, Any]]] = None
    attention_candidate: bool = False
    follow_up_candidate: bool = False
    context: Optional[dict[str, Any]] = None
    temporal_start: datetime
    temporal_end: datetime
