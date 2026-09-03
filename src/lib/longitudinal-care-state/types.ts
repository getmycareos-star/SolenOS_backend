import type { CanonicalCareEvent } from "../situation-entry/types";

/**
 * LONGITUDINAL CARE STATE — IRREDUCIBLE MODEL
 *
 * L = (D, A, B, τ)
 *
 * D = set of care-relevant dimensions
 * A = set of time-bounded, evidence-linked assertions
 * B = set of baselines per dimension
 * τ = set of state transitions
 */

export const CARE_STATE_DIMENSIONS = [
  "active_conditions",
  "resolved_conditions",
  "symptoms",
  "functional_status",
  "cognitive_status",
  "medications",
  "allergies",
  "treatments",
  "procedures",
  "care_dependencies",
  "mobility",
  "living_situation",
  "care_relationships",
  "providers",
  "pending_situations",
  "risks",
  "restrictions",
  "goals",
  "functional_baseline",
  "cognitive_baseline",
] as const;

export type CareStateDimension = (typeof CARE_STATE_DIMENSIONS)[number];

export const CARE_STATE_VALUES: Record<CareStateDimension, readonly string[]> = {
  active_conditions: [],
  resolved_conditions: [],
  symptoms: [],
  functional_status: ["independent", "requires_assistance", "dependent", "unknown"],
  cognitive_status: ["intact", "mild_impairment", "moderate_impairment", "severe_impairment", "unknown"],
  medications: [],
  allergies: [],
  treatments: [],
  procedures: [],
  care_dependencies: ["none", "minimal", "moderate", "high", "total"],
  mobility: ["independent", "requires_assistance", "wheelchair", "bedbound", "unknown"],
  living_situation: ["home_alone", "home_with_caregiver", "assisted_living", "nursing_home", "hospital", "unknown"],
  care_relationships: [],
  providers: [],
  pending_situations: [],
  risks: [],
  restrictions: [],
  goals: [],
  functional_baseline: ["independent", "requires_assistance", "dependent", "unknown"],
  cognitive_baseline: ["intact", "mild_impairment", "moderate_impairment", "severe_impairment", "unknown"],
};

export const CARE_STATE_STATUSES = [
  "active",
  "resolved",
  "suspended",
  "unknown",
] as const;

export type CareStateStatus = (typeof CARE_STATE_STATUSES)[number];

export const CARE_STATE_CONFLICT_STATUSES = [
  "coexisting",
  "superseded",
  "corrected",
  "invalidated",
] as const;

export type CareStateConflictStatus = (typeof CARE_STATE_CONFLICT_STATUSES)[number];

export const BASELINE_DIMENSIONS = [
  "functional",
  "cognitive",
  "mobility",
  "medication",
  "symptom",
  "behavioral",
] as const;

export type BaselineDimension = (typeof BASELINE_DIMENSIONS)[number];

export const TRANSITION_MECHANISMS = [
  "new_evidence",
  "clinical_event",
  "intervention",
  "natural_progression",
  "correction",
  "retirement",
  "baseline_shift",
] as const;

export type TransitionMechanism = (typeof TRANSITION_MECHANISMS)[number];

export type CareStateAssertion = {
  id: string;
  dimension: CareStateDimension;
  value: string;
  status: CareStateStatus;
  validity_start: string;
  validity_end: string | null;
  confidence: number;
  evidence_ids: string[];
  /** CanonicalCareEvent ids that directly support this assertion */
  event_ids: string[];
  /** Baseline reference if this assertion represents a deviation */
  baseline_id?: string;
  /** If this assertion corrects or supersedes another */
  supersedes_id?: string;
  superseded_by_id?: string;
  /** Conflict status — coexisting when multiple valid assertions exist */
  conflict_status: CareStateConflictStatus;
  /** Why this assertion exists */
  provenance_note: string;
  created_at: string;
  updated_at: string;
  care_recipient_id: string;
  caregiver_id?: string;
};

export type CareStateBaseline = {
  id: string;
  dimension: BaselineDimension;
  care_state_dimension: CareStateDimension;
  value: string;
  established_at: string;
  last_confirmed_at: string;
  evidence_ids: string[];
  event_ids: string[];
  confidence: number;
  /** Is this a pre-event or post-event baseline */
  context: "pre_event" | "post_event" | "stable_period";
  /** Reference event that caused this baseline to be established */
  reference_event_id?: string;
  /** Previous baseline if this replaced one */
  supersedes_baseline_id?: string;
  care_recipient_id: string;
  created_at: string;
};

export type CareStateTransition = {
  id: string;
  care_recipient_id: string;
  occurred_at: string;
  /** Assertions that ended */
  from_assertion_ids: string[];
  /** Assertions that began */
  to_assertion_ids: string[];
  mechanism: TransitionMechanism;
  confidence: number;
  /** Events that caused or evidence this transition */
  evidence_ids: string[];
  event_ids: string[];
  /** Whether this transition was explicitly observed or reconstructed */
  detection_method: "explicit" | "reconstructed" | "inferred";
  /** Human-readable explanation */
  description: string;
  created_at: string;
};

export type CareStateConflict = {
  id: string;
  care_recipient_id: string;
  dimension: CareStateDimension;
  assertion_ids: string[];
  detected_at: string;
  resolved: boolean;
  resolution?: {
    winning_assertion_id: string;
    reason: string;
    resolved_at: string;
  };
};

export type CareStateDelta = {
  id: string;
  care_recipient_id: string;
  computed_at: string;
  from_time: string;
  to_time: string;
  additions: CareStateAssertion[];
  removals: CareStateAssertion[];
  modifications: {
    assertion_id: string;
    dimension: CareStateDimension;
    from_value: string;
    to_value: string;
  }[];
  /** Did the system learn something new vs actual state change */
  learning_type: "new_observation" | "retroactive_correction" | "reconstruction";
  description: string;
};

export type CareStateSnapshot = {
  id: string;
  care_recipient_id: string;
  computed_at: string;
  as_of_time: string;
  assertions: CareStateAssertion[];
  baselines: CareStateBaseline[];
  transition_count: number;
  confidence_summary: number;
  /** Materialized for performance — reconstructable from assertion store */
  materialized: boolean;
};

export type StateReconstructionRequest = {
  care_recipient_id: string;
  as_of_time: string;
  include_baselines: boolean;
  include_transitions: boolean;
  include_deltas: boolean;
};

export type StateReconstructionResult = {
  care_recipient_id: string;
  as_of_time: string;
  state: CareStateSnapshot;
  /** Dimensions where no assertion covers this time */
  unknown_dimensions: CareStateDimension[];
  /** Conflicts active at this time */
  conflicts: CareStateConflict[];
  /** Transitions within [as_of_time - window, as_of_time] */
  recent_transitions: CareStateTransition[];
  /** Delta from previous reconstruction point if available */
  delta?: CareStateDelta;
  /** Temporal gaps in the assertion record */
  gaps: { dimension: CareStateDimension; from: string; to: string }[];
  confidence: number;
};

export type MeaningfulChangeClassification = {
  is_meaningful: boolean;
  category: "clinical" | "functional" | "administrative" | "data_quality" | "unknown";
  severity: "high" | "medium" | "low" | "none";
  reason: string;
  requires_review: boolean;
};

export type LongitudinalCareState = {
  care_recipient_id: string;
  assertions: Map<string, CareStateAssertion>;
  baselines: Map<string, CareStateBaseline>;
  transitions: CareStateTransition[];
  conflicts: CareStateConflict[];
  /** Reconstruction cache keyed by ISO timestamp */
  reconstruction_cache: Map<string, StateReconstructionResult>;
  created_at: string;
  updated_at: string;
};

export type CareStateStoreSnapshot = {
  care_recipient_id: string;
  assertion_count: number;
  baseline_count: number;
  transition_count: number;
  conflict_count: number;
  last_reconstructed_at: string | null;
};
