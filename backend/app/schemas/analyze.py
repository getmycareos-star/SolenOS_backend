from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional, List, Dict, Any, Literal
from enum import Enum


class AnalysisSourceType(str, Enum):
    caregiver_entry = "caregiver_entry"
    document = "document"
    voice = "voice"
    structured = "structured"


class AnalyzeRequest(BaseModel):
    input: str
    source_type: AnalysisSourceType = AnalysisSourceType.caregiver_entry
    person_id: str
    caregiver_id: Optional[str] = None
    telemetry_user_id: Optional[str] = None
    care_session_id: Optional[str] = None
    language_preference: Optional[str] = None
    governance_settings: Optional[Dict[str, Any]] = None
    prior_input_raw: Optional[str] = None
    resume_context: Optional[str] = None


class TrustLayerResult(BaseModel):
    known: Dict[str, Any]
    assumed: Dict[str, Any]
    unknown: Dict[str, Any]
    confidence: float


class CrisisPreventionResult(BaseModel):
    risk_level: Literal["low", "medium", "high", "critical"]
    triggers: List[str]
    recommended_actions: List[str]


class SituationRiskRegisterResult(BaseModel):
    risks: List[Dict[str, Any]]
    overall_score: float
    critical_count: int


class MissingInformationQueueResult(BaseModel):
    gaps: List[Dict[str, Any]]
    priority_questions: List[str]


class CaregiverLoadResult(BaseModel):
    load_score: float
    depletion_signals: List[str]
    burden_breakdown: Dict[str, float]


class ResponsibilityGraphResult(BaseModel):
    responsibilities: List[Dict[str, Any]]
    gaps: List[Dict[str, Any]]
    conflicts: List[Dict[str, Any]]


class DelegationResult(BaseModel):
    delegations: List[Dict[str, Any]]
    pending_acceptance: List[str]


class EpistemicSafetyResult(BaseModel):
    safety_violations: List[str]
    confidence_threshold_met: bool
    medical_boundary_crossed: bool


class MeetingPrepResult(BaseModel):
    agenda: List[str]
    questions_to_ask: List[str]
    documents_to_bring: List[str]


class CognitiveReliefResult(BaseModel):
    load_reduction: float
    simplification: str
    priority_items: List[str]


class CareJourneyGraphResult(BaseModel):
    nodes: List[Dict[str, Any]]
    edges: List[Dict[str, Any]]
    timeline: List[Dict[str, Any]]


class MemoryReconstructionResult(BaseModel):
    reconstructed_memories: List[Dict[str, Any]]
    confidence: float
    gaps: List[str]


class AnalyzeResponse(BaseModel):
    # Core output
    result: Dict[str, Any]
    
    # Layer outputs
    trust_layer: Optional[TrustLayerResult] = None
    crisis_prevention_layer: Optional[CrisisPreventionResult] = None
    situation_risk_register_layer: Optional[SituationRiskRegisterResult] = None
    missing_information_queue_layer: Optional[MissingInformationQueueResult] = None
    caregiver_load_layer: Optional[CaregiverLoadResult] = None
    responsibility_graph_layer: Optional[ResponsibilityGraphResult] = None
    delegation_layer: Optional[DelegationResult] = None
    epistemic_safety_layer: Optional[EpistemicSafetyResult] = None
    meeting_prep_layer: Optional[MeetingPrepResult] = None
    cognitive_relief_layer: Optional[CognitiveReliefResult] = None
    care_journey_graph_layer: Optional[CareJourneyGraphResult] = None
    memory_reconstruction_layer: Optional[MemoryReconstructionResult] = None
    
    # Meta
    risk_level: Literal["low", "medium", "high", "critical"]
    latency_ms: int
    care_session_id: str
    interaction_id: Optional[str] = None
    user_id: Optional[str] = None
    continuity_layer: Optional[Dict[str, Any]] = None


class RiskAssessmentRequest(BaseModel):
    person_id: str
    situation_text: str
    context: Optional[Dict[str, Any]] = None


class RiskAssessmentResponse(BaseModel):
    risk_level: Literal["low", "medium", "high", "critical"]
    factors: List[str]
    situation_risk_register: SituationRiskRegisterResult
    crisis_prevention: CrisisPreventionResult


class UncertaintyAssessmentRequest(BaseModel):
    person_id: str
    claims: List[str]
    context: Optional[Dict[str, Any]] = None


class UncertaintyAssessmentResponse(BaseModel):
    gaps: MissingInformationQueueResult
    trust_layer: TrustLayerResult
    epistemic_safety: EpistemicSafetyResult


class LoadAssessmentRequest(BaseModel):
    caregiver_id: str
    person_id: str
    time_window_hours: int = 168


class LoadAssessmentResponse(BaseModel):
    caregiver_load: CaregiverLoadResult
    responsibility_graph: ResponsibilityGraphResult
    delegation: DelegationResult


class MeetingPrepRequest(BaseModel):
    person_id: str
    meeting_type: str
    scheduled_at: Optional[datetime] = None


class MemoryReconstructionRequest(BaseModel):
    person_id: str
    query: str
    time_range: Optional[Dict[str, datetime]] = None


class CareJourneyRequest(BaseModel):
    person_id: str
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None