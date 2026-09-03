/**
 * Dementia-Specific Intelligence — strict object model.
 *
 * HARD INVARIANT (enforced structurally):
 *   - No observation, pattern, situation, or context object may carry a
 *     diagnosis, stage, subtype, etiology, prognosis, or treatment field.
 *   - These are not "unset"; they do not exist in the type. The only place
 *     such terms may appear is as preserved verbatim quoted text from
 *     source inputs, never as a system-derived conclusion.
 *
 * Everything here is observed, attributable, qualified, and reconstructable.
 * See ./qualification-firewall.ts for runtime enforcement.
 */

import { z } from "zod";

// ─── Source / Provenance ──────────────────────────────────────────────────

export const SourceTypeSchema = z.enum([
  "caregiver",
  "clinician",
  "patient",
  "facility_staff",
  "paid_caregiver",
  "administrative",
  "unknown",
]);
export type SourceType = z.infer<typeof SourceTypeSchema>;

export const ConcernStrengthSchema = z.enum([
  "vague_concern",
  "specific_observation",
  "quantified_event",
  "clinically_documented",
]);
export type ConcernStrength = z.infer<typeof ConcernStrengthSchema>;

/**
 * Provenance — the evidence chain for a single observation.
 * Carries the original text, observer, time, and source strength.
 */
export const ProvenanceSchema = z.object({
  source_type: SourceTypeSchema,
  observer_id: z.string().nullable(),
  observed_at: z.string().nullable(),
  raw_text: z.string(),
  /** When the observation was first captured by SolenOS */
  captured_at: z.string(),
  /** When the source itself was authored, if known */
  source_authored_at: z.string().nullable(),
  /** Optional document/note pointer */
  document_id: z.string().nullable(),
});
export type Provenance = z.infer<typeof ProvenanceSchema>;

// ─── Care Context ─────────────────────────────────────────────────────────

/**
 * Care-context activation model.
 *
 * Distinct from any clinical interpretation. The context is what determines
 * whether dementia-specific intelligence should interpret observations as
 * care-relevant, and at what strength. The context is NOT a diagnosis.
 */
export const ContextStrengthSchema = z.enum([
  "none",
  "concern_only",
  "under_investigation",
  "established",
]);
export type ContextStrength = z.infer<typeof ContextStrengthSchema>;

export const DementiaSubtypeSchema = z.enum([
  "alzheimer_disease",
  "vascular_dementia",
  "lewy_body_dementia",
  "frontotemporal_dementia",
  "mixed_dementia",
  "unspecified_dementia",
  "other",
]);
export type DementiaSubtype = z.infer<typeof DementiaSubtypeSchema>;

/**
 * Care Context — activation and strength of dementia-care interpretation.
 *
 * `subtype` is stored verbatim input only. SolenOS never infers subtype from
 * observations. If the caregiver or clinician literally wrote "Alzheimer's"
 * in a note, we keep that token as input context; it does not constrain or
 * predict any future observation's interpretation.
 */
export const DementiaCareContextSchema = z.object({
  context_id: z.string(),
  /** Whether a diagnosis is explicitly documented in the source corpus */
  documented_dementia: z.boolean(),
  /** Verbatim subtype from source, never inferred */
  documented_subtype: DementiaSubtypeSchema.nullable(),
  /** Family/caregiver-reported concern exists */
  caregiver_concern: z.boolean(),
  /** Suspected (not yet evaluated) cognitive impairment */
  suspected_cognitive_impairment: z.boolean(),
  /** An active cognitive-care workflow exists */
  active_cognitive_care_workflow: z.boolean(),
  /** The interpreted strength of the context */
  context_strength: ContextStrengthSchema,
  /** Whether a clinical evaluation is pending */
  pending_evaluation: z.boolean(),
  /** Verbatim text containing a diagnosis, if any. NEVER used for inference. */
  diagnosis_quote: z.string().nullable(),
  /** The source where the diagnosis text was extracted from, if any */
  diagnosis_quote_provenance: ProvenanceSchema.nullable(),
});
export type DementiaCareContext = z.infer<typeof DementiaCareContextSchema>;

// ─── Domain types (classification only — NOT assessment) ──────────────────

export const CognitiveDomainSchema = z.enum([
  "memory",
  "orientation",
  "attention",
  "language",
  "executive",
  "judgment",
  "visuospatial",
  "recognition",
  "problem_solving",
]);
export type CognitiveDomain = z.infer<typeof CognitiveDomainSchema>;

export const ObservationDomainSchema = z.enum([
  "cognition",
  "behavior",
  "function",
  "safety",
]);
export type ObservationDomain = z.infer<typeof ObservationDomainSchema>;

// ─── Observation Types ────────────────────────────────────────────────────

export const CognitiveObservationTypeSchema = z.enum([
  "repeated_question",
  "lost_objects",
  "forgot_recent_event",
  "forgot_instruction",
  "unable_to_retain",
  "repeated_story",
  "word_finding",
  "conversation_following",
]);
export type CognitiveObservationType = z.infer<typeof CognitiveObservationTypeSchema>;

export const OrientationAspectSchema = z.enum([
  "to_person",
  "to_place",
  "to_time",
  "to_situation",
]);
export type OrientationAspect = z.infer<typeof OrientationAspectSchema>;

export const ConfusionAttributeSchema = z.object({
  onset: z.enum(["acute", "gradual", "unknown"]),
  duration: z.string().nullable(),
  context: z.string().nullable(),
  trigger: z.string().nullable(),
  behavior: z.string().nullable(),
  resolution: z.string().nullable(),
  recurrence: z.boolean(),
});
export type ConfusionAttribute = z.infer<typeof ConfusionAttributeSchema>;

export const BehavioralObservationTypeSchema = z.enum([
  "agitation",
  "withdrawal",
  "aggression",
  "apathy",
  "repetitive_behavior",
  "nighttime_activity",
  "inappropriate_behavior",
  "routine_disruption",
  "sundowning_like_pattern",
]);
export type BehavioralObservationType = z.infer<typeof BehavioralObservationTypeSchema>;

export const FunctionalObservationTypeSchema = z.enum([
  "bathing",
  "dressing",
  "toileting",
  "eating",
  "mobility",
  "grooming",
  "medication_self_management",
  "finances",
  "transportation",
  "cooking",
  "shopping",
  "appointments",
  "communication_device",
  "household_management",
]);
export type FunctionalObservationType = z.infer<typeof FunctionalObservationTypeSchema>;

export const SafetyObservationTypeSchema = z.enum([
  "wandering",
  "elopement",
  "stove_left_on",
  "unsafe_driving",
  "hazard_nonresponse",
  "door_unlocked",
  "unsafe_medication_use",
  "navigation_impairment",
  "getting_lost",
  "unexpected_departure",
  "purposeful_travel",
]);
export type SafetyObservationType = z.infer<typeof SafetyObservationTypeSchema>;

export const MedicationManagementAspectSchema = z.enum([
  "forgot_to_take",
  "double_dosed",
  "missed_doses",
  "unable_to_organize",
  "requires_caregiver_administration",
  "requires_caregiver_prompting",
  "requires_caregiver_setup",
]);
export type MedicationManagementAspect = z.infer<typeof MedicationManagementAspectSchema>;

// ─── Independence Model ───────────────────────────────────────────────────

/**
 * 6-level functional independence. Crucially:
 *   prompting ≠ supervision ≠ assistance ≠ dependence
 * The structural distinction is what makes care-relevant situations
 * distinguishable from each other.
 */
export const IndependenceLevelSchema = z.enum([
  "independent",
  "needs_prompting",
  "needs_supervision",
  "needs_assistance",
  "dependent",
  "unknown",
]);
export type IndependenceLevel = z.infer<typeof IndependenceLevelSchema>;

/**
 * Independence ordering — used for change detection (does NOT diagnose).
 * Source: prompts are still capable of action; supervision is monitoring;
 * assistance is partial physical help; dependence is full takeover.
 */
export const INDEPENDENCE_ORDER: readonly IndependenceLevel[] = [
  "independent",
  "needs_prompting",
  "needs_supervision",
  "needs_assistance",
  "dependent",
  "unknown",
];

export function independenceRank(level: IndependenceLevel): number {
  return INDEPENDENCE_ORDER.indexOf(level);
}

export function isIndependenceDecline(from: IndependenceLevel, to: IndependenceLevel): boolean {
  if (from === "unknown" || to === "unknown") return false;
  return independenceRank(to) > independenceRank(from);
}

// ─── Core Observation ─────────────────────────────────────────────────────

/**
 * A single, source-attributed observation.
 * This is the most primitive unit. It is NOT an interpretation.
 */
export const CognitiveObservationSchema = z.object({
  observation_id: z.string(),
  subject_id: z.string(),
  observation_type: CognitiveObservationTypeSchema,
  cognitive_domain: CognitiveDomainSchema,
  observed_behavior: z.string(),
  concern_strength: ConcernStrengthSchema,
  observation_confidence: z.enum(["low", "medium", "high"]),
  observation_time: z.string().nullable(),
  provenance: ProvenanceSchema,
  /** Optional disambiguation for repeated-question events */
  orientation_aspect: OrientationAspectSchema.nullable().optional(),
  /** Optional quantified attribute (e.g. "asked 7 times") */
  quantifier: z
    .object({
      count: z.number().int().nonnegative().nullable(),
      window_start: z.string().nullable(),
      window_end: z.string().nullable(),
    })
    .nullable()
    .optional(),
});
export type CognitiveObservation = z.infer<typeof CognitiveObservationSchema>;

export const ConfusionObservationSchema = z.object({
  observation_id: z.string(),
  subject_id: z.string(),
  observed_behavior: z.string(),
  concern_strength: ConcernStrengthSchema,
  observation_confidence: z.enum(["low", "medium", "high"]),
  observation_time: z.string().nullable(),
  provenance: ProvenanceSchema,
  attributes: ConfusionAttributeSchema,
});
export type ConfusionObservation = z.infer<typeof ConfusionObservationSchema>;

export const BehavioralObservationSchema = z.object({
  observation_id: z.string(),
  subject_id: z.string(),
  observation_type: BehavioralObservationTypeSchema,
  observed_behavior: z.string(),
  concern_strength: ConcernStrengthSchema,
  observation_confidence: z.enum(["low", "medium", "high"]),
  observation_time: z.string().nullable(),
  provenance: ProvenanceSchema,
  context_tags: z.array(z.string()).default([]),
});
export type BehavioralObservation = z.infer<typeof BehavioralObservationSchema>;

/**
 * Functional observation — captures a single observed activity + level.
 * NOTE: functional change is `observed level` only; cause is left untyped.
 */
export const FunctionalObservationSchema = z.object({
  observation_id: z.string(),
  subject_id: z.string(),
  activity: FunctionalObservationTypeSchema,
  observed_independence: IndependenceLevelSchema,
  observed_behavior: z.string(),
  concern_strength: ConcernStrengthSchema,
  observation_confidence: z.enum(["low", "medium", "high"]),
  observation_time: z.string().nullable(),
  provenance: ProvenanceSchema,
  /** Optional medication-management aspect if this is a medication-self-management activity */
  medication_aspect: MedicationManagementAspectSchema.nullable().optional(),
  /** Whether a baseline is known for this activity */
  baseline_present: z.boolean().default(false),
});
export type FunctionalObservation = z.infer<typeof FunctionalObservationSchema>;

export const SafetyObservationSchema = z.object({
  observation_id: z.string(),
  subject_id: z.string(),
  observation_type: SafetyObservationTypeSchema,
  observed_behavior: z.string(),
  concern_strength: ConcernStrengthSchema,
  observation_confidence: z.enum(["low", "medium", "high"]),
  observation_time: z.string().nullable(),
  provenance: ProvenanceSchema,
  /** Acute vs chronic vs recurrent */
  temporal_class: z.enum(["acute", "chronic", "recurrent", "unknown"]).default("unknown"),
  /** Care/supervision consequence observed, never capacity/legal */
  care_consequence: z
    .enum([
      "none",
      "increased_supervision",
      "restricted_access",
      "active_monitoring",
      "unknown",
    ])
    .default("unknown"),
});
export type SafetyObservation = z.infer<typeof SafetyObservationSchema>;

// ─── Functional Change (baseline → current) ───────────────────────────────

/**
 * A functional change record. Always reconstructable to its component
 * observations. Never implies cause. Baseline is required; absence of
 * baseline → `unknown`, not "assumed independent".
 */
export const FunctionalChangeSchema = z.object({
  change_id: z.string(),
  subject_id: z.string(),
  activity: FunctionalObservationTypeSchema,
  baseline: z.object({
    independence: IndependenceLevelSchema,
    baseline_time: z.string().nullable(),
    baseline_provenance: ProvenanceSchema.nullable(),
  }),
  current: z.object({
    independence: IndependenceLevelSchema,
    current_time: z.string().nullable(),
    current_provenance: ProvenanceSchema,
  }),
  /** Direction — declined / improved / unchanged / unknown */
  direction: z.enum(["decline", "improvement", "unchanged", "unknown"]),
  /** Whether the change crosses a care-relevance threshold (>=1 level shift) */
  care_relevant: z.boolean(),
  /** Cause is intentionally NOT a field. Never inferred. */
});
export type FunctionalChange = z.infer<typeof FunctionalChangeSchema>;

// ─── Pattern Aggregation ──────────────────────────────────────────────────

/**
 * A pattern is a temporal aggregation of independent observations.
 * The threshold is parameterized; no hardcoded count.
 */
export const PatternSchema = z.object({
  pattern_id: z.string(),
  subject_id: z.string(),
  domain: ObservationDomainSchema,
  pattern_kind: z.string(),
  /** Underlying observations — every pattern is fully reconstructable */
  component_observation_ids: z.array(z.string()).min(1),
  /** Window of the pattern */
  window_start: z.string().nullable(),
  window_end: z.string().nullable(),
  /** Count of independent (de-duped) observations */
  independent_observation_count: z.number().int().nonnegative(),
  /** Pattern-level confidence tier (NOT clinical confidence) */
  pattern_confidence: z.enum(["low", "medium", "high"]),
  /** Acute vs chronic */
  temporal_class: z.enum(["acute", "recurring", "chronic", "unknown"]),
  /** Source attribution — how many distinct sources */
  distinct_source_count: z.number().int().nonnegative(),
  /** Direction, if a baseline exists */
  direction: z.enum(["increasing", "decreasing", "stable", "unknown"]).default("unknown"),
});
export type Pattern = z.infer<typeof PatternSchema>;

// ─── Care-Relevant Situation ──────────────────────────────────────────────

/**
 * The care-relevant situation is the cross-domain synthesis unit.
 *
 * It may be a single event with care consequence OR a pattern of
 * cross-domain observations. A situation REQUIRES at least one of:
 *   - a functional consequence
 *   - a safety consequence
 *   - a documented care-relevance tag
 */
export const CareRelevantSituationSchema = z.object({
  situation_id: z.string(),
  subject_id: z.string(),
  situation_label: z.string(),
  /** Component observations and patterns */
  component_observation_ids: z.array(z.string()).min(1),
  component_pattern_ids: z.array(z.string()).default([]),
  /** Domains involved — single-domain is allowed but not cross-domain */
  domains: z.array(ObservationDomainSchema).min(1),
  /** Whether the situation spans more than one domain */
  cross_domain: z.boolean(),
  /** Functional consequences observed (not causes) */
  functional_consequences: z.array(z.string()).default([]),
  /** Safety consequences observed (not causes) */
  safety_consequences: z.array(z.string()).default([]),
  /** Care-relevance tier */
  care_relevance: z.enum(["low", "medium", "high"]),
  /** Context strength at the time of synthesis */
  context_strength: ContextStrengthSchema,
  /** Whether the situation flags an acute change for evaluation */
  acute_change_flag: z.boolean().default(false),
  /** Pending evaluation flag */
  pending_evaluation: z.boolean().default(false),
  /** Provenance chain — every claim is reconstructable */
  evidence_chain: z.array(ProvenanceSchema),
  /** When the situation was last synthesized */
  synthesized_at: z.string(),
});
export type CareRelevantSituation = z.infer<typeof CareRelevantSituationSchema>;

// ─── Negative / Absent Observation ────────────────────────────────────────

/**
 * Negative observation — a domain was checked and found absent within a
 * window. Crucial: "no report" ≠ "did not occur". Absent observations
 * carry low confidence and an explicit reporting window.
 */
export const AbsentObservationSchema = z.object({
  observation_id: z.string(),
  subject_id: z.string(),
  observed_behavior: z.string(),
  domain: ObservationDomainSchema,
  reporting_window_start: z.string(),
  reporting_window_end: z.string(),
  confidence: z.enum(["low", "medium", "high"]),
  provenance: ProvenanceSchema,
});
export type AbsentObservation = z.infer<typeof AbsentObservationSchema>;

// ─── Source Disagreement ──────────────────────────────────────────────────

/**
 * Surfaced (not resolved) disagreement between observers. Contradiction
 * resolution belongs to the upstream Contradiction Intelligence. This is
 * a minimal surface so DSI can carry the disagreement into situations.
 */
export const SourceDisagreementSchema = z.object({
  disagreement_id: z.string(),
  subject_id: z.string(),
  topic: z.string(),
  conflicting_observations: z.array(z.string()).min(2),
  source_breakdown: z.array(
    z.object({
      source_id: z.string(),
      source_type: SourceTypeSchema,
      observation_id: z.string(),
    }),
  ),
  surfaced_at: z.string(),
});
export type SourceDisagreement = z.infer<typeof SourceDisagreementSchema>;

// ─── Aggregation Bundle ───────────────────────────────────────────────────

/**
 * The output of the dementia-specific intelligence pipeline for a subject.
 * It is a read-model — never the source of truth. Every contained object
 * is reconstructable.
 */
export const DSIProjectionSchema = z.object({
  subject_id: z.string(),
  care_context: DementiaCareContextSchema,
  cognitive_observations: z.array(CognitiveObservationSchema).default([]),
  confusion_observations: z.array(ConfusionObservationSchema).default([]),
  behavioral_observations: z.array(BehavioralObservationSchema).default([]),
  functional_observations: z.array(FunctionalObservationSchema).default([]),
  safety_observations: z.array(SafetyObservationSchema).default([]),
  absent_observations: z.array(AbsentObservationSchema).default([]),
  functional_changes: z.array(FunctionalChangeSchema).default([]),
  patterns: z.array(PatternSchema).default([]),
  care_relevant_situations: z.array(CareRelevantSituationSchema).default([]),
  source_disagreements: z.array(SourceDisagreementSchema).default([]),
  synthesized_at: z.string(),
});
export type DSIProjection = z.infer<typeof DSIProjectionSchema>;
