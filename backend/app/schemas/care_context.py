from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.core.care_context_enums import (
    ConversationStage,
    ContinuityDirection,
    DecisionOwner,
    DecisionStatus,
    EngagementTrigger,
    EngagementUrgency,
    FollowUpStatus,
    FollowUpTrigger,
    HandoffReason,
    MattersCategory,
    MattersImportance,
    PlanStatus,
    PersonAttributeDomain,
    ProfileCompleteness,
)
from app.core.llm_enums import LLMReasoningMode, LLMStatus, LLMTask


# ----------------------------------------------------------------------
# Local reasoning
# ----------------------------------------------------------------------


class LLMHealthResponse(BaseModel):
    enabled: bool
    available: bool
    status: LLMStatus
    base_url: str
    model: str
    circuit: str
    consecutive_failures: int
    detail: Optional[str] = None


class LLMCallResponse(BaseModel):
    id: str
    person_id: Optional[str] = None
    task: LLMTask
    mode: LLMReasoningMode
    model: str
    status: LLMStatus
    guard_verdict: Optional[str] = None
    guard_reasons: Optional[List[str]] = None
    input_evidence_ids: Optional[List[str]] = None
    output_evidence_ids: Optional[List[str]] = None
    dropped_evidence_ids: Optional[List[str]] = None
    latency_ms: Optional[int] = None
    is_fallback: bool
    error_detail: Optional[str] = None
    called_at: datetime

    class Config:
        from_attributes = True


class LLMReasoningResultResponse(BaseModel):
    id: str
    person_id: str
    task: LLMTask
    subject_type: Optional[str] = None
    subject_id: Optional[str] = None
    summary: str
    detail: Optional[List[Dict[str, Any]]] = None
    evidence_ids: List[str]
    possible_context: Optional[str] = None
    confidence: Optional[float] = Field(None, ge=0.0, le=1.0)
    guard_verdict: Optional[str] = None
    qualifications: Optional[List[str]] = None
    created_at: datetime

    class Config:
        from_attributes = True


class LLMReasonRequest(BaseModel):
    task: LLMTask
    person_id: str
    payload: Dict[str, Any] = Field(default_factory=dict)
    evidence_ids: Optional[List[str]] = None
    subject_type: Optional[str] = None
    subject_id: Optional[str] = None
    mode: LLMReasoningMode = LLMReasoningMode.LLM_ASSISTED
    allow_diagnostic: bool = False
    allow_medical_action: bool = False
    require_evidence: bool = True


class LLMReasonResponse(BaseModel):
    ok: bool
    result: Optional[LLMReasoningResultResponse] = None
    message: str
    warnings: Optional[List[str]] = None


# ----------------------------------------------------------------------
# Person model
# ----------------------------------------------------------------------


class PersonAttributeCreate(BaseModel):
    person_id: str
    domain: PersonAttributeDomain = PersonAttributeDomain.OTHER
    label: str
    value: str
    evidence_ids: Optional[List[str]] = None
    source_quote: Optional[str] = None
    stated_by_caregiver_id: Optional[str] = None
    confidence: Optional[float] = Field(None, ge=0.0, le=1.0)
    possible_context: Optional[str] = None


class PersonAttributeResponse(BaseModel):
    id: str
    person_id: str
    domain: PersonAttributeDomain
    label: str
    value: str
    source_quote: Optional[str] = None
    evidence_ids: Optional[List[str]] = None
    confidence: Optional[float] = Field(None, ge=0.0, le=1.0)
    possible_context: Optional[str] = None
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True


class PersonTriggerCreate(BaseModel):
    person_id: str
    trigger: str
    category: Optional[str] = None
    response_seen: Optional[str] = None
    occurrences: Optional[int] = None
    calming_measure: Optional[str] = None
    evidence_ids: Optional[List[str]] = None
    last_observed_at: Optional[str] = None
    confidence: Optional[float] = Field(None, ge=0.0, le=1.0)
    possible_context: Optional[str] = None


class PersonTriggerResponse(BaseModel):
    id: str
    person_id: str
    trigger: str
    category: Optional[str] = None
    response_seen: Optional[str] = None
    occurrences: Optional[int] = None
    calming_measure: Optional[str] = None
    evidence_ids: Optional[List[str]] = None
    confidence: Optional[float] = Field(None, ge=0.0, le=1.0)
    possible_context: Optional[str] = None
    is_active: bool

    class Config:
        from_attributes = True


class PersonCalmerCreate(BaseModel):
    person_id: str
    measure: str
    context: Optional[str] = None
    evidence_ids: Optional[List[str]] = None
    confidence: Optional[float] = Field(None, ge=0.0, le=1.0)


class PersonCalmerResponse(BaseModel):
    id: str
    person_id: str
    measure: str
    context: Optional[str] = None
    evidence_ids: Optional[List[str]] = None
    confidence: Optional[float] = Field(None, ge=0.0, le=1.0)
    is_active: bool

    class Config:
        from_attributes = True


class PersonProfileResponse(BaseModel):
    id: str
    person_id: str
    completeness: ProfileCompleteness
    attribute_count: int
    trigger_count: int
    calmer_count: int
    summary: str
    personalisation: Optional[str] = None
    communication_style: Optional[str] = None
    what_settles_them: Optional[List[str]] = None
    what_upsets_them: Optional[List[str]] = None
    important_people: Optional[List[Dict[str, Any]]] = None
    evidence_ids: List[str]
    possible_context: Optional[str] = None
    confidence: Optional[float] = Field(None, ge=0.0, le=1.0)
    created_at: datetime

    class Config:
        from_attributes = True


# ----------------------------------------------------------------------
# Continuity
# ----------------------------------------------------------------------


class ContinuityThreadResponse(BaseModel):
    id: str
    person_id: str
    subject: str
    label: str
    observation_count: int
    first_recorded_at: Optional[datetime] = None
    last_recorded_at: Optional[datetime] = None
    direction: ContinuityDirection
    is_stale: Optional[bool] = None
    stale_since: Optional[datetime] = None
    is_active: bool

    class Config:
        from_attributes = True


class ContinuitySnapshotResponse(BaseModel):
    id: str
    person_id: str
    subject: Optional[str] = None
    thread_id: Optional[str] = None
    previously_known: Optional[List[Dict[str, Any]]] = None
    currently_known: Optional[List[Dict[str, Any]]] = None
    what_will_matter_next: Optional[List[Dict[str, Any]]] = None
    gaps: Optional[List[Dict[str, Any]]] = None
    evidence_ids: List[str]
    possible_context: Optional[str] = None
    confidence: Optional[float] = Field(None, ge=0.0, le=1.0)
    created_at: datetime

    class Config:
        from_attributes = True


class ContinuityHandoffCreate(BaseModel):
    person_id: str
    reason: HandoffReason = HandoffReason.CAREGIVER_CHANGE
    from_caregiver_id: Optional[str] = None
    to_caregiver_id: Optional[str] = None
    setting_change_note: Optional[str] = None


class ContinuityHandoffResponse(BaseModel):
    id: str
    person_id: str
    reason: HandoffReason
    from_caregiver_id: Optional[str] = None
    to_caregiver_id: Optional[str] = None
    setting_change_note: Optional[str] = None
    whats_changed: Optional[List[Dict[str, Any]]] = None
    person_in_brief: Optional[str] = None
    what_goes_wrong: Optional[List[Dict[str, Any]]] = None
    open_threads: Optional[List[str]] = None
    first_24_hours: Optional[List[str]] = None
    covering_since: Optional[datetime] = None
    gap_days: Optional[int] = None
    evidence_ids: List[str]
    possible_context: Optional[str] = None
    confidence: Optional[float] = Field(None, ge=0.0, le=1.0)
    is_acknowledged: bool
    created_at: datetime

    class Config:
        from_attributes = True


# ----------------------------------------------------------------------
# What matters
# ----------------------------------------------------------------------


class WhatMattersCreate(BaseModel):
    person_id: str
    statement: str
    category: MattersCategory = MattersCategory.VALUE
    importance: MattersImportance = MattersImportance.MODERATE
    who_it_matters_to: Optional[str] = None
    how_we_know: Optional[str] = None
    what_it_rules_out: Optional[List[str]] = None
    source_quote: Optional[str] = None
    stated_by_caregiver_id: Optional[str] = None
    evidence_ids: Optional[List[str]] = None
    confidence: Optional[float] = Field(None, ge=0.0, le=1.0)
    possible_context: Optional[str] = None


class WhatMattersResponse(BaseModel):
    id: str
    person_id: str
    category: MattersCategory
    statement: str
    who_it_matters_to: Optional[str] = None
    how_we_know: Optional[str] = None
    importance: MattersImportance
    what_it_rules_out: Optional[List[str]] = None
    source_quote: Optional[str] = None
    attributed_to_person: bool
    evidence_ids: Optional[List[str]] = None
    confidence: Optional[float] = Field(None, ge=0.0, le=1.0)
    possible_context: Optional[str] = None
    is_active: bool

    class Config:
        from_attributes = True


class AlignmentRequest(BaseModel):
    person_id: str
    action_description: str
    action_ref_type: Optional[str] = None
    action_ref_id: Optional[str] = None


# ----------------------------------------------------------------------
# Trajectory
# ----------------------------------------------------------------------


class TrajectoryPointCreate(BaseModel):
    person_id: str
    subject: str
    observed_at: Optional[str] = None
    value: Optional[float] = None
    value_text: Optional[str] = None
    evidence_ids: Optional[List[str]] = None
    source_text: Optional[str] = None
    observed_by_caregiver_id: Optional[str] = None
    is_estimate: bool = False


class TrajectoryResponse(BaseModel):
    id: str
    person_id: str
    subject: str
    label: str
    unit: Optional[str] = None
    direction: str
    slope_per_week: Optional[float] = None
    spread: Optional[float] = None
    point_count: int
    window_start: Optional[datetime] = None
    window_end: Optional[datetime] = None
    is_volatile: Optional[bool] = None
    plain_reading: Optional[str] = None
    what_it_could_mean: Optional[List[Dict[str, Any]]] = None
    if_it_continues: Optional[str] = None
    worth_watching_for: Optional[str] = None
    baseline_variation_explains: Optional[bool] = None
    evidence_ids: List[str]
    possible_context: Optional[str] = None
    confidence: Optional[float] = Field(None, ge=0.0, le=1.0)
    is_active: bool

    class Config:
        from_attributes = True


class TrajectoryTrackRequest(BaseModel):
    person_id: str
    subject: str
    label: Optional[str] = None
    unit: Optional[str] = None
    baseline_variation: Optional[float] = Field(None, ge=0.0)


# ----------------------------------------------------------------------
# Behaviour
# ----------------------------------------------------------------------


class BehaviorObservationCreate(BaseModel):
    person_id: str
    description: str
    category: str = "other"
    original_text: Optional[str] = None
    occurred_at: Optional[str] = None
    time_of_day: Optional[str] = None
    location: Optional[str] = None
    observed_by_caregiver_id: Optional[str] = None
    preceding_context: Optional[str] = None
    response_taken: Optional[str] = None
    duration_minutes: Optional[int] = None
    evidence_ids: Optional[List[str]] = None
    is_safety_concern: Optional[bool] = None


class BehaviorInterpretationResponse(BaseModel):
    id: str
    person_id: str
    observation_id: str
    hypotheses: List[Dict[str, Any]]
    hypothesis_count: int
    is_complete: bool
    what_would_settle_it: Optional[List[Dict[str, Any]]] = None
    pattern_match: Optional[str] = None
    summary: str
    evidence_ids: List[str]
    possible_context: Optional[str] = None
    confidence: Optional[float] = Field(None, ge=0.0, le=1.0)
    requires_human_review: bool
    created_at: datetime

    class Config:
        from_attributes = True


class BehaviorHypothesisReject(BaseModel):
    reason: str
    caregiver_id: Optional[str] = None


# ----------------------------------------------------------------------
# Contradiction
# ----------------------------------------------------------------------


class ContradictionAnalysisResponse(BaseModel):
    id: str
    person_id: str
    contradiction_id: Optional[str] = None
    primary_kind: Optional[str] = None
    likely_explanations: Optional[List[Dict[str, Any]]] = None
    what_to_ask: Optional[str] = None
    who_can_resolve: Optional[str] = None
    do_not_resolve_by: Optional[str] = None
    summary: str
    recommended_path: Optional[str] = None
    is_auto_resolvable: bool
    evidence_ids: List[str]
    possible_context: Optional[str] = None
    confidence: Optional[float] = Field(None, ge=0.0, le=1.0)
    created_at: datetime

    class Config:
        from_attributes = True


class ContradictionResolutionCreate(BaseModel):
    analysis_id: str
    path_taken: str
    outcome: str
    reason: Optional[str] = None
    remaining_uncertainty: Optional[str] = None
    caregiver_id: Optional[str] = None


class ContradictionClose(BaseModel):
    notes: str
    caregiver_id: Optional[str] = None


# ----------------------------------------------------------------------
# Care planning
# ----------------------------------------------------------------------


class CareDecisionCreate(BaseModel):
    person_id: str
    question: str
    context: Optional[str] = None
    urgency: Optional[str] = None
    evidence_ids: Optional[List[str]] = None
    subject_type: Optional[str] = None
    subject_id: Optional[str] = None


class CareDecisionResponse(BaseModel):
    id: str
    person_id: str
    question: str
    context: Optional[str] = None
    urgency: Optional[str] = None
    status: DecisionStatus
    owner: DecisionOwner
    options: Optional[List[Dict[str, Any]]] = None
    option_count: int
    recommendation: Optional[Dict[str, Any]] = None
    deciding_factors: Optional[List[str]] = None
    summary: str
    safety_decision: Optional[str] = None
    requires_clinician: bool
    requires_human_review: bool
    evidence_ids: List[str]
    possible_context: Optional[str] = None
    confidence: Optional[float] = Field(None, ge=0.0, le=1.0)
    decided_at: Optional[datetime] = None
    decision_note: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class CareDecisionOutcome(BaseModel):
    accept: bool
    note: Optional[str] = None
    caregiver_id: Optional[str] = None


class CarePlanCreate(BaseModel):
    person_id: str
    decision_id: Optional[str] = None
    title: Optional[str] = None
    evidence_ids: Optional[List[str]] = None


class CarePlanResponse(BaseModel):
    id: str
    person_id: str
    decision_id: Optional[str] = None
    title: str
    description: Optional[str] = None
    status: PlanStatus
    steps: Optional[List[Dict[str, Any]]] = None
    step_count: int
    review_at: Optional[datetime] = None
    stop_and_escalate_if: Optional[List[str]] = None
    if_it_is_not_working: Optional[str] = None
    summary: str
    evidence_ids: List[str]
    possible_context: Optional[str] = None
    confidence: Optional[float] = Field(None, ge=0.0, le=1.0)
    created_at: datetime

    class Config:
        from_attributes = True


# ----------------------------------------------------------------------
# Follow-ups
# ----------------------------------------------------------------------


class FollowUpCreate(BaseModel):
    person_id: str
    what_to_recheck: str
    trigger: FollowUpTrigger = FollowUpTrigger.UNCERTAINTY
    interval_days: Optional[int] = Field(None, ge=1, le=365)
    due_at: Optional[str] = None
    why_open: Optional[str] = None
    what_result_changes_the_plan: Optional[str] = None
    subject_type: Optional[str] = None
    subject_id: Optional[str] = None
    field: Optional[str] = None
    evidence_ids: Optional[List[str]] = None
    cadence: Optional[str] = None
    priority_hint: Optional[str] = None


class FollowUpResponse(BaseModel):
    id: str
    person_id: str
    subject_type: Optional[str] = None
    subject_id: Optional[str] = None
    field: Optional[str] = None
    what_to_recheck: str
    why_open: Optional[str] = None
    what_result_changes_the_plan: Optional[str] = None
    trigger: FollowUpTrigger
    status: FollowUpStatus
    due_at: datetime
    interval_days: Optional[int] = None
    cadence: Optional[str] = None
    evidence_ids: Optional[List[str]] = None
    possible_context: Optional[str] = None
    confidence: Optional[float] = Field(None, ge=0.0, le=1.0)
    completed_at: Optional[datetime] = None
    outcome: Optional[str] = None

    class Config:
        from_attributes = True


class FollowUpComplete(BaseModel):
    outcome: str
    caregiver_id: Optional[str] = None


class FollowUpReschedule(BaseModel):
    due_at: str
    why: Optional[str] = None


# ----------------------------------------------------------------------
# Proactive
# ----------------------------------------------------------------------


class EngagementOpportunityResponse(BaseModel):
    id: str
    person_id: str
    trigger: EngagementTrigger
    subject_type: Optional[str] = None
    subject_id: Optional[str] = None
    should_reach_out: bool
    reason_now: Optional[str] = None
    what_to_say: Optional[str] = None
    urgency: EngagementUrgency
    why_quiet_is_better: Optional[str] = None
    channel: Optional[str] = None
    quiet_hours_respected: bool
    suppressed: bool
    suppression_reason: Optional[str] = None
    evidence_ids: List[str]
    possible_context: Optional[str] = None
    confidence: Optional[float] = Field(None, ge=0.0, le=1.0)
    is_acted_on: bool
    acted_at: Optional[datetime] = None
    created_at: datetime

    class Config:
        from_attributes = True


# ----------------------------------------------------------------------
# Conversation
# ----------------------------------------------------------------------


class ConversationMessage(BaseModel):
    person_id: str
    text: str
    session_id: Optional[str] = None
    caregiver_id: Optional[str] = None


class ConversationResponse(BaseModel):
    session_id: str
    person_id: str
    reply: str
    stage: ConversationStage
    evidence_ids: List[str]
    possible_context: Optional[str] = None
    confidence: Optional[float] = Field(None, ge=0.0, le=1.0)
    guard_verdict: Optional[str] = None
    safety_note: Optional[str] = None
    reasoned_locally: bool
    # True when no answer was produced because the local model did not respond.
    # This is not the same as "the record does not support an answer", and the
    # two must not be shown to a caregiver as the same thing: the first is a
    # fault in this system, the second is a fact about their mother. Without the
    # flag the UI can only render one ambiguous "no answer" and the outage
    # reads as a gap in the record.
    unavailable: bool = False
