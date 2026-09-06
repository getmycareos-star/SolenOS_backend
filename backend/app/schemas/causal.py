from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional, List, Any, Dict
from app.core.causal_enums import (
    EvidenceStrength,
    CausalConfidence,
    CausalRelationshipStatus,
    CausalDirection,
    EvidenceRole,
    EvidenceType,
)


class CausalHypothesisBase(BaseModel):
    person_id: str
    potential_cause_type: str
    potential_cause_id: Optional[str] = None
    potential_cause_description: str
    outcome_type: str
    outcome_id: Optional[str] = None
    outcome_description: str
    causal_direction: CausalDirection = CausalDirection.UNKNOWN
    relationship_status: CausalRelationshipStatus = CausalRelationshipStatus.OBSERVED_ASSOCIATION
    evidence_strength: EvidenceStrength = EvidenceStrength.WEAK
    causal_confidence: CausalConfidence = CausalConfidence.INSUFFICIENT
    temporal_relationship: Optional[str] = None
    time_gap_seconds: Optional[float] = None
    is_repeatable: bool = False
    repeat_count: Optional[int] = None
    repeat_pattern: Optional[str] = None
    supporting_evidence_ids: Optional[List[str]] = None
    contradictory_evidence_ids: Optional[List[str]] = None
    evidence_ids: Optional[List[str]] = None
    first_observed_date: Optional[datetime] = None
    last_evaluated_date: Optional[datetime] = None
    provenance: Optional[str] = None
    is_active: bool = True
    superseded_by_hypothesis_id: Optional[str] = None
    created_by_caregiver_id: Optional[str] = None
    updated_by_caregiver_id: Optional[str] = None


class CausalHypothesisCreate(CausalHypothesisBase):
    pass


class CausalHypothesisUpdate(BaseModel):
    potential_cause_description: Optional[str] = None
    outcome_description: Optional[str] = None
    causal_direction: Optional[CausalDirection] = None
    relationship_status: Optional[CausalRelationshipStatus] = None
    evidence_strength: Optional[EvidenceStrength] = None
    causal_confidence: Optional[CausalConfidence] = None
    temporal_relationship: Optional[str] = None
    time_gap_seconds: Optional[float] = None
    is_repeatable: Optional[bool] = None
    repeat_count: Optional[int] = None
    repeat_pattern: Optional[str] = None
    supporting_evidence_ids: Optional[List[str]] = None
    contradictory_evidence_ids: Optional[List[str]] = None
    evidence_ids: Optional[List[str]] = None
    first_observed_date: Optional[datetime] = None
    last_evaluated_date: Optional[datetime] = None
    provenance: Optional[str] = None
    is_active: Optional[bool] = None
    superseded_by_hypothesis_id: Optional[str] = None
    updated_by_caregiver_id: Optional[str] = None


class CausalHypothesis(CausalHypothesisBase):
    id: str
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class CausalEvidenceBase(BaseModel):
    hypothesis_id: str
    person_id: str
    role: EvidenceRole = EvidenceRole.SUPPORTING
    evidence_type: EvidenceType
    strength: EvidenceStrength = EvidenceStrength.WEAK
    description: str
    source_document: Optional[str] = None
    source_event_id: Optional[str] = None
    evidence_ref_id: Optional[str] = None
    caregiver_id: Optional[str] = None
    timestamp: Optional[datetime] = None
    is_derived: bool = False
    derivation_method: Optional[str] = None


class CausalEvidenceCreate(CausalEvidenceBase):
    pass


class CausalEvidence(CausalEvidenceBase):
    id: str
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class AlternativeExplanationBase(BaseModel):
    hypothesis_id: str
    person_id: str
    explanation_type: str
    description: str
    plausibility: CausalConfidence = CausalConfidence.POSSIBLE
    evidence_ids: Optional[List[str]] = None
    is_active: bool = True
    created_by_caregiver_id: Optional[str] = None


class AlternativeExplanationCreate(AlternativeExplanationBase):
    pass


class AlternativeExplanationUpdate(BaseModel):
    description: Optional[str] = None
    plausibility: Optional[CausalConfidence] = None
    evidence_ids: Optional[List[str]] = None
    is_active: Optional[bool] = None


class AlternativeExplanation(AlternativeExplanationBase):
    id: str
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class ConfounderBase(BaseModel):
    hypothesis_id: str
    person_id: str
    factor_type: str
    description: str
    plausibility: CausalConfidence = CausalConfidence.POSSIBLE
    evidence_ids: Optional[List[str]] = None
    is_active: bool = True
    created_by_caregiver_id: Optional[str] = None


class ConfounderCreate(ConfounderBase):
    pass


class Confounder(ConfounderBase):
    id: str
    created_at: datetime

    class Config:
        from_attributes = True


class TemporalAssociation(BaseModel):
    cause_event_id: Optional[str] = None
    outcome_event_id: Optional[str] = None
    cause_type: str
    cause_id: Optional[str] = None
    cause_description: str
    cause_time: Optional[datetime] = None
    outcome_type: str
    outcome_id: Optional[str] = None
    outcome_description: str
    outcome_time: Optional[datetime] = None
    relation_type: str
    time_difference_seconds: Optional[float] = None


class CausalEvidenceSummary(BaseModel):
    role: str
    evidence_type: str
    strength: str
    description: str
    source_document: Optional[str] = None
    source_event_id: Optional[str] = None
    timestamp: Optional[datetime] = None
    is_derived: bool
    derivation_method: Optional[str] = None


class CausalAnalysisResult(BaseModel):
    person_id: str

    what_happened: str
    what_followed: str
    why_it_may_be_relevant: str
    what_else_is_known: List[str] = Field(default_factory=list)
    what_is_uncertain: str
    what_may_warrant_attention: str

    potential_cause_type: str
    potential_cause_description: str
    outcome_type: str
    outcome_description: str

    temporal_relationship: Optional[str] = None
    time_gap_seconds: Optional[float] = None
    causal_direction: CausalDirection = CausalDirection.UNKNOWN

    relationship_status: CausalRelationshipStatus
    evidence_strength: EvidenceStrength
    causal_confidence: CausalConfidence

    supporting_evidence: List[CausalEvidenceSummary] = Field(default_factory=list)
    contradictory_evidence: List[CausalEvidenceSummary] = Field(default_factory=list)
    alternative_explanations: List[Dict[str, Any]] = Field(default_factory=list)
    confounders: List[Dict[str, Any]] = Field(default_factory=list)

    language_used: List[str] = Field(default_factory=list)
    quality_gate_passed: bool
    quality_gate_notes: List[str] = Field(default_factory=list)

    hypothesis_id: Optional[str] = None
    first_observed_date: Optional[datetime] = None
    last_evaluated_date: Optional[datetime] = None
