/**
 * Attention / Decision Lifecycle Types
 * 
 * Core invariant: SolenOS manages attention as a stateful lifecycle, not notifications.
 * Every consequential issue has an explicit attention state driven by evidence.
 */

import type { AttentionClass, AttentionPriority } from "../attention-engine/types";

/** ============================================
 *  CARE DOMAIN CALIBRATION TYPES
 *  ============================================ */

export type CareDomain = 
  | "appetite_nutrition"
  | "confusion_cognition"
  | "mobility_falls"
  | "sleep"
  | "mood_behavior"
  | "medication"
  | "elimination"
  | "skin_integrity"
  | "pain"
  | "social_engagement"
  | "fear_of_falling"
  | "falls_risk"
  | "caregiver_burnout"
  | "self_harm_risk"
  | "dehydration"
  | "infection_risk"
  | "cognitive_decline"
  | "swallowing_risk"
  | "other";

export type CareDomainSignal = {
  domain: CareDomain;
  signal_type: string;
  description: string;
  baseline_deviation: "mild" | "moderate" | "severe";
  clinical_significance: "low" | "medium" | "high" | "critical";
  typical_escalation_timeline_hours: number;
  requires_clinical_input: boolean;
  common_cascades: CareDomain[];
};

export type ClinicalRiskProfile = {
  overall_risk: number; // 0-1
  domains: CareDomain[];
  cascades: CareDomain[];
  requires_clinical_review: boolean;
  escalation_timeline_hours: number;
};

/** ============================================
 *  1. ATTENTION STATES — explicit lifecycle states
 *  ============================================ */

export type AttentionState =
  | "NOT_RELEVANT"
  | "BACKGROUND"
  | "WATCH"
  | "NEEDS_ATTENTION"
  | "HIGH_PRIORITY"
  | "URGENT"
  | "EMERGENCY"
  | "AWAITING_INFORMATION"
  | "AWAITING_DECISION"
  | "AWAITING_ACTION"
  | "IN_PROGRESS"
  | "AWAITING_VERIFICATION"
  | "RESOLVED"
  | "CLOSED"
  | "SUPERSEDED";

export const ATTENTION_STATE_ORDER: ReadonlyArray<AttentionState> = [
  "NOT_RELEVANT",
  "BACKGROUND",
  "WATCH",
  "NEEDS_ATTENTION",
  "HIGH_PRIORITY",
  "URGENT",
  "EMERGENCY",
  "AWAITING_INFORMATION",
  "AWAITING_DECISION",
  "AWAITING_ACTION",
  "IN_PROGRESS",
  "AWAITING_VERIFICATION",
  "RESOLVED",
  "CLOSED",
  "SUPERSEDED",
] as const;

/**
 * Rank map for attention states based on clinical urgency, grouping
 * alert states and waiting states by their urgency level so that
 * e.g. AWAITING_DECISION (rank 4) is not considered more urgent than
 * HIGH_PRIORITY (rank 4) or URGENT (rank 5).
 */
export const ATTENTION_STATE_RANKS: Readonly<Record<AttentionState, number>> = {
  NOT_RELEVANT: 0,
  BACKGROUND: 1,
  WATCH: 2,
  NEEDS_ATTENTION: 3,
  AWAITING_INFORMATION: 3,
  HIGH_PRIORITY: 4,
  AWAITING_DECISION: 4,
  AWAITING_VERIFICATION: 4,
  URGENT: 5,
  AWAITING_ACTION: 5,
  IN_PROGRESS: 5,
  EMERGENCY: 6,
  RESOLVED: 2,
  CLOSED: 1,
  SUPERSEDED: 0,
};

export const ACTIVE_ATTENTION_STATES: ReadonlyArray<AttentionState> = [
  "WATCH",
  "NEEDS_ATTENTION",
  "HIGH_PRIORITY",
  "URGENT",
  "EMERGENCY",
  "AWAITING_INFORMATION",
  "AWAITING_DECISION",
  "AWAITING_ACTION",
  "IN_PROGRESS",
  "AWAITING_VERIFICATION",
] as const;

export const TERMINAL_ATTENTION_STATES: ReadonlyArray<AttentionState> = [
  "RESOLVED",
  "CLOSED",
  "SUPERSEDED",
] as const;

export function isActiveAttentionState(state: AttentionState): boolean {
  return ACTIVE_ATTENTION_STATES.includes(state);
}

export function isTerminalAttentionState(state: AttentionState): boolean {
  return TERMINAL_ATTENTION_STATES.includes(state);
}

export function attentionStateRank(state: AttentionState): number {
  return ATTENTION_STATE_RANKS[state] ?? -1;
}

export function compareAttentionStates(a: AttentionState, b: AttentionState): number {
  return attentionStateRank(a) - attentionStateRank(b);
}

/** ============================================
 *  2. ATTENTION DIMENSIONS — multidimensional assessment
 *  ============================================ */

export type AttentionDimension =
  | "magnitude"
  | "acuteness"
  | "risk"
  | "baseline_deviation"
  | "persistence"
  | "recurrence"
  | "trend"
  | "functional_impact"
  | "cognitive_behavioral_impact"
  | "medication_implications"
  | "caregiver_capacity"
  | "open_loop_dependency"
  | "time_pressure"
  | "uncertainty"
  | "evidence_quality"
  | "action_already_taken";

export type AttentionDimensionScore = {
  dimension: AttentionDimension;
  score: number;
  evidence: string;
  weight: number;
};

export type AttentionAssessment = {
  assessment_id: string;
  timestamp: string;
  dimension_scores: AttentionDimensionScore[];
  composite_score: number;
  recommended_state: AttentionState;
  confidence: number;
  reasoning: string;
};

/** ============================================
 *  3. DECISION LIFECYCLE STATES
 *  ============================================ */

export type DecisionLifecycleState =
  | "NOT_REQUIRED"
  | "DECISION_NEEDED"
  | "DECISION_BLOCKED"
  | "AWAITING_HUMAN_DECISION"
  | "AWAITING_CLINICAL_DECISION"
  | "DECISION_MADE"
  | "ACTION_PENDING"
  | "ACTION_IN_PROGRESS"
  | "ACTION_COMPLETED"
  | "OUTCOME_UNKNOWN"
  | "VERIFICATION_PENDING"
  | "RESOLVED"
  | "SUPERSEDED";

export const DECISION_LIFECYCLE_ORDER: ReadonlyArray<DecisionLifecycleState> = [
  "NOT_REQUIRED",
  "DECISION_NEEDED",
  "DECISION_BLOCKED",
  "AWAITING_HUMAN_DECISION",
  "AWAITING_CLINICAL_DECISION",
  "DECISION_MADE",
  "ACTION_PENDING",
  "ACTION_IN_PROGRESS",
  "ACTION_COMPLETED",
  "OUTCOME_UNKNOWN",
  "VERIFICATION_PENDING",
  "RESOLVED",
  "SUPERSEDED",
] as const;

export function decisionLifecycleRank(state: DecisionLifecycleState): number {
  const idx = DECISION_LIFECYCLE_ORDER.indexOf(state);
  return idx >= 0 ? idx : -1;
}

/** ============================================
 *  4. ATTENTION OWNERSHIP
 *  ============================================ */

export type AttentionOwner =
  | "caregiver"
  | "another_family_caregiver"
  | "clinician"
  | "pharmacy"
  | "facility"
  | "patient"
  | "shared_family_responsibility"
  | "unknown_unassigned";

export type OwnershipAssignment = {
  owner: AttentionOwner;
  assigned_at: string;
  assigned_by: string;
  responsibility: string;
  contact_info?: string;
};

/** ============================================
 *  5. ATTENTION ITEM — core entity
 *  ============================================ */

export type AttentionItem = {
  id: string;
  care_recipient_id: string;
  title: string;
  description: string;
  /** The signal/change that triggered this attention item */
  triggering_signal: string;
  /** Current attention state */
  state: AttentionState;
  /** Current decision lifecycle state */
  decision_state: DecisionLifecycleState;
  /** Multidimensional assessment */
  assessment: AttentionAssessment;
  /** Owner responsible for action */
  ownership: OwnershipAssignment | null;
  /** Dependencies on other attention items */
  dependencies: AttentionDependency[];
  /** Items that depend on this one */
  dependents: string[];
  /** History of state transitions */
  history: AttentionHistoryEntry[];
  /** Whether this item has been superseded by another */
  superseded_by: string | null;
  /** Whether this item supersedes another */
  supersedes: string | null;
  /** Tags for categorization */
  tags: string[];
  /** Related care event IDs */
  related_event_ids: string[];
  /** Created timestamp */
  created_at: string;
  /** Last updated timestamp */
  updated_at: string;
};

/** ============================================
 *  6. ATTENTION DEPENDENCY
 *  ============================================ */

export type DependencyType =
  | "blocks_decision"
  | "blocks_action"
  | "requires_verification"
  | "requires_information"
  | "requires_resolution"
  | "provides_context";

export type AttentionDependency = {
  dependency_id: string;
  depends_on_item_id: string;
  type: DependencyType;
  description: string;
  /** Whether the dependency is currently satisfied */
  satisfied: boolean;
  /** What would satisfy this dependency */
  satisfaction_criteria: string;
};

/** ============================================
 *  7. ATTENTION HISTORY — audit trail
 *  ============================================ */

export type AttentionHistoryEntry = {
  history_id: string;
  timestamp: string;
  item_id: string;
  from_state: AttentionState | null;
  to_state: AttentionState;
  from_decision_state: DecisionLifecycleState | null;
  to_decision_state: DecisionLifecycleState;
  trigger: HistoryTrigger;
  actor: string;
  evidence: string[];
  reasoning: string;
  decision_made?: string;
  action_taken?: string;
  outcome?: string;
};

export type HistoryTrigger =
  | "signal_detected"
  | "assessment_updated"
  | "escalation"
  | "de_escalation"
  | "decision_required"
  | "decision_made"
  | "action_started"
  | "action_completed"
  | "verification_started"
  | "verification_completed"
  | "resolution_confirmed"
  | "closure"
  | "reopening"
  | "superseded"
  | "dependency_satisfied"
  | "dependency_blocked"
  | "ownership_assigned"
  | "ownership_changed"
  | "manual_override"
  | "periodic_review";

/** ============================================
 *  8. ATTENTION LIFECYCLE CONFIGURATION
 *  ============================================ */

export type AttentionLifecycleConfig = {
  /** Minimum time before de-escalation can occur (ms) */
  min_deescalation_interval_ms: number;
  /** Maximum time before attention auto-decays (ms) */
  max_attention_duration_ms: number;
  /** Threshold for escalation based on composite score */
  escalation_thresholds: Record<AttentionState, number>;
  /** Threshold for de-escalation based on composite score */
  deescalation_thresholds: Record<AttentionState, number>;
  /** Dimensions and their weights for composite scoring */
  dimension_weights: Record<AttentionDimension, number>;
  /** Enable automatic escalation/de-escalation */
  auto_lifecycle: boolean;
  /** Enable dependency-aware attention */
  dependency_aware: boolean;
  /** Enable attention history audit trail */
  audit_trail: boolean;
};

/** Default configuration */
export const DEFAULT_ATTENTION_LIFECYCLE_CONFIG: AttentionLifecycleConfig = {
  min_deescalation_interval_ms: 3600000,
  max_attention_duration_ms: 604800000,
  escalation_thresholds: {
    NOT_RELEVANT: 0,
    BACKGROUND: 0.1,
    WATCH: 0.25,
    NEEDS_ATTENTION: 0.4,
    HIGH_PRIORITY: 0.6,
    URGENT: 0.75,
    EMERGENCY: 0.9,
    AWAITING_INFORMATION: 0.3,
    AWAITING_DECISION: 0.4,
    AWAITING_ACTION: 0.5,
    IN_PROGRESS: 0.5,
    AWAITING_VERIFICATION: 0.4,
    RESOLVED: 0.1,
    CLOSED: 0,
    SUPERSEDED: 0,
  },
  deescalation_thresholds: {
    NOT_RELEVANT: 0,
    BACKGROUND: 0.05,
    WATCH: 0.15,
    NEEDS_ATTENTION: 0.25,
    HIGH_PRIORITY: 0.35,
    URGENT: 0.5,
    EMERGENCY: 0.7,
    AWAITING_INFORMATION: 0.2,
    AWAITING_DECISION: 0.3,
    AWAITING_ACTION: 0.4,
    IN_PROGRESS: 0.3,
    AWAITING_VERIFICATION: 0.2,
    RESOLVED: 0.05,
    CLOSED: 0,
    SUPERSEDED: 0,
  },
  dimension_weights: {
    magnitude: 0.15,
    acuteness: 0.12,
    risk: 0.18,
    baseline_deviation: 0.1,
    persistence: 0.1,
    recurrence: 0.08,
    trend: 0.08,
    functional_impact: 0.1,
    cognitive_behavioral_impact: 0.08,
    medication_implications: 0.1,
    caregiver_capacity: 0.07,
    open_loop_dependency: 0.12,
    time_pressure: 0.1,
    uncertainty: 0.08,
    evidence_quality: 0.07,
    action_already_taken: 0.1,
  },
  auto_lifecycle: true,
  dependency_aware: true,
  audit_trail: true,
};

/** ============================================
 *  9. FAILURE TAXONOMY — for benchmarking
 *  ============================================ */

export type AttentionFailureType =
  | "ATTENTION_MISSED"
  | "FALSE_ATTENTION"
  | "ATTENTION_OVER_ESCALATION"
  | "ATTENTION_UNDER_ESCALATION"
  | "KEYWORD_BASED_ATTENTION"
  | "BASELINE_BLIND_ATTENTION"
  | "HISTORY_BLIND_ATTENTION"
  | "RESOLVED_ATTENTION_RETAINED"
  | "UNRESOLVED_ATTENTION_CLEARED"
  | "ATTENTION_PERSISTENCE_ERROR"
  | "ATTENTION_DECAY_FAILURE"
  | "ESCALATION_FAILURE"
  | "DEESCALATION_FAILURE"
  | "REOPENING_FAILURE"
  | "FALSE_REOPENING"
  | "DECISION_LIFECYCLE_FAILURE"
  | "DECISION_AS_RESOLUTION"
  | "ACTION_AS_RESOLUTION"
  | "RECOMMENDATION_AS_COMPLETION"
  | "COMPLETION_AS_VERIFICATION"
  | "OUTCOME_UNKNOWN_AS_SUCCESS"
  | "OWNER_LOSS"
  | "OWNER_INVENTION"
  | "DEPENDENCY_FAILURE"
  | "BLOCKED_DECISION_MISSED"
  | "TEMPORAL_PRESSURE_MISSED"
  | "ATTENTION_COMPETITION_FAILURE"
  | "DUPLICATE_ATTENTION"
  | "ATTENTION_HISTORY_LOSS"
  | "ATTENTION_PROVENANCE_LOSS"
  | "ATTENTION_UNCERTAINTY_LOSS"
  | "UNSUPPORTED_ESCALATION"
  | "UNSUPPORTED_DEESCALATION"
  | "UNSAFE_ATTENTION_ACTION"
  | "CLINICAL_URGENCY_AS_DIAGNOSIS"
  | "OPEN_LOOP_ATTENTION_DISCONNECT"
  | "DECISION_ATTENTION_DISCONNECT";

export type AttentionBenchmarkCase = {
  case_id: string;
  name: string;
  description: string;
  input: BenchmarkInput;
  expected: BenchmarkExpected;
  failure_modes: AttentionFailureType[];
};

export type BenchmarkInput = {
  signals: BenchmarkSignal[];
  timeline: BenchmarkTimelineEntry[];
  existing_items: string[];
  context: BenchmarkContext;
};

export type BenchmarkSignal = {
  signal_id: string;
  timestamp: string;
  type: string;
  description: string;
  evidence: string;
  magnitude?: number;
};

export type BenchmarkTimelineEntry = {
  day: number;
  description: string;
  signals: string[];
  expected_state: AttentionState;
  expected_decision_state: DecisionLifecycleState;
};

export type BenchmarkExpected = {
  final_state: AttentionState;
  final_decision_state: DecisionLifecycleState;
  expected_owner: AttentionOwner | null;
  expected_dependencies: string[];
  should_escalate: boolean;
  should_deescalate: boolean;
  should_close: boolean;
  should_reopen: boolean;
};

export type BenchmarkContext = {
  care_recipient_id: string;
  baseline: string;
  caregiver_capacity: "high" | "medium" | "low" | "unknown";
  clinical_context: string;
};

/** ============================================
 *  10. ATTENTION COMPETITION RESOLUTION
 *  ============================================ */

export type CompetitionResolution = {
  primary_item_id: string;
  reasoning: string;
  suppressed_items: SuppressedAttentionItem[];
};

export type SuppressedAttentionItem = {
  item_id: string;
  reason: "lower_priority" | "dependent_on_primary" | "already_handled" | "background" | "duplicate";
  detail: string;
};