/**
 * Dementia-Specific Intelligence — public surface.
 *
 * DSI is a thin interpretive overlay that runs in a dementia-care context.
 * It does NOT diagnose, stage, or treat. It only:
 *   1. Activates by care-context strength.
 *   2. Classifies observations into cognitive/behavioral/functional/safety.
 *   3. De-duplicates and aggregates into patterns.
 *   4. Synthesizes care-relevant situations with provenance.
 *   5. Surfaces (never resolves) source disagreements.
 *
 * Every emitted claim passes through the qualification firewall
 * (./qualification-firewall.ts) and is qualified by tier
 * (observation | event | pattern | situation | clinical_concern).
 * Forbidden tiers (clinical_inference, diagnosis, staging, etiology,
 * prognosis, treatment_recommendation, capacity_conclusion,
 * legal_conclusion) are not produced by this module.
 */

// Types
export type {
  SourceType,
  ConcernStrength,
  Provenance,
  ContextStrength,
  DementiaSubtype,
  DementiaCareContext,
  CognitiveDomain,
  ObservationDomain,
  CognitiveObservationType,
  OrientationAspect,
  ConfusionAttribute,
  BehavioralObservationType,
  FunctionalObservationType,
  SafetyObservationType,
  MedicationManagementAspect,
  IndependenceLevel,
  CognitiveObservation,
  ConfusionObservation,
  BehavioralObservation,
  FunctionalObservation,
  SafetyObservation,
  FunctionalChange,
  Pattern,
  CareRelevantSituation,
  AbsentObservation,
  SourceDisagreement,
  DSIProjection,
} from "./types";

export {
  // Independence helpers
  INDEPENDENCE_ORDER,
  independenceRank,
  isIndependenceDecline,
  // Zod schemas for runtime validation
  ProvenanceSchema,
  DementiaCareContextSchema,
  CognitiveObservationSchema,
  ConfusionObservationSchema,
  BehavioralObservationSchema,
  FunctionalObservationSchema,
  SafetyObservationSchema,
  FunctionalChangeSchema,
  PatternSchema,
  CareRelevantSituationSchema,
  AbsentObservationSchema,
  SourceDisagreementSchema,
  DSIProjectionSchema,
  IndependenceLevelSchema,
  SourceTypeSchema,
  ConcernStrengthSchema,
  ContextStrengthSchema,
  DementiaSubtypeSchema,
  CognitiveDomainSchema,
  ObservationDomainSchema,
  CognitiveObservationTypeSchema,
  OrientationAspectSchema,
  ConfusionAttributeSchema,
  BehavioralObservationTypeSchema,
  FunctionalObservationTypeSchema,
  SafetyObservationTypeSchema,
  MedicationManagementAspectSchema,
} from "./types";

// Qualification firewall
export {
  FORBIDDEN_CLAIM_PATTERNS,
  QUALIFICATION_TIERS,
  FORBIDDEN_TIERS,
  QualificationFirewallViolation,
  findForbiddenClaimMatch,
  assertClaimAllowed,
  assertNoForbiddenFields,
  assertQualificationTier,
  assertProvenancePresent,
  emitClaim,
} from "./qualification-firewall";
export type { QualificationTier, ForbiddenTier, FirewallMode } from "./qualification-firewall";

// Context
export {
  buildDementiaCareContext,
  buildProvenance,
  isDementiaContextActive,
  isActiveCognitiveCareWorkflow,
  CONTEXT_STRENGTH_SEMANTICS,
  DIAGNOSIS_ACTIVATION_PATTERNS,
  SUSPECTED_IMPAIRMENT_PATTERNS,
  CAREGIVER_CONCERN_PATTERNS,
  DementiaCareContextSchema as CareContextSchema,
} from "./context";
export type { BuildContextInput, BuildProvenanceInput, ActivationTrigger } from "./context";

// Independence
export {
  ADL_ACTIVITIES,
  IADL_ACTIVITIES,
  classifyActivity,
  INDEPENDENCE_CUES,
  classifyIndependenceFromText,
  buildFunctionalChange,
} from "./independence";
export type { ADLCategory, IndependenceClassification, BuildFunctionalChangeInput } from "./independence";

// Observations
export {
  inferConcernStrength,
  detectQuantifier,
  extractCognitiveObservations,
  extractConfusionObservations,
  extractBehavioralObservations,
  extractFunctionalObservations,
  extractSafetyObservations,
} from "./observations";
export type {
  StrengthFromTextInput,
  ExtractCognitiveParams,
  ExtractConfusionParams,
  ExtractBehavioralParams,
  ExtractFunctionalParams,
  ExtractSafetyParams,
} from "./observations";

// Repeated events / patterns
export {
  DEFAULT_PATTERN_CONFIG,
  makeDedupeKey,
  dedupeObservations,
  buildPatternFromObservations,
  detectRepeatedQuestionPattern,
  detectConfusionEpisodePattern,
  detectBehaviorPattern,
  detectSafetyPattern,
  detectFunctionalPattern,
} from "./repeated-events";
export type {
  PatternConfig,
  DedupeKey,
  PatternCandidate,
  RepeatedQuestionResult,
  ConfusionEpisodePatternResult,
} from "./repeated-events";

// Care-relevance
export {
  assessCareRelevance,
  buildCareRelevanceClaim,
  computeSourceAgreement,
  buildEvidenceChain,
} from "./care-relevance";
export type {
  CareRelevanceInput,
  CareRelevanceAssessment,
  SourceAgreementInput,
  CareRelevantSituationShape,
} from "./care-relevance";

// Situations
export {
  synthesizeCareRelevantSituation,
  synthesizeAllCareRelevantSituations,
  surfaceSourceDisagreement,
  detectAcuteChange,
} from "./situations";
export type { BuildSituationInput } from "./situations";

// Language
export {
  synthesizeSafeSituationLabel,
  synthesizeSafeSituationDescription,
  ALLOWED_PHRASE_PATTERNS,
  FORBIDDEN_PHRASE_PATTERNS,
  isAllowedPhrase,
} from "./language";
export type { SituationLabelInput, SituationDescriptionInput } from "./language";

// ─── Top-level orchestration ──────────────────────────────────────────────

import type {
  CognitiveObservation,
  ConfusionObservation,
  BehavioralObservation,
  FunctionalObservation,
  SafetyObservation,
  FunctionalChange,
  Pattern,
  DSIProjection,
  AbsentObservation,
  SourceDisagreement,
  CareRelevantSituation,
  DementiaCareContext,
} from "./types";
import { DSIProjectionSchema } from "./types";
import {
  detectRepeatedQuestionPattern,
  detectConfusionEpisodePattern,
  detectBehaviorPattern,
  detectSafetyPattern,
  detectFunctionalPattern,
} from "./repeated-events";
import {
  synthesizeCareRelevantSituation,
  detectAcuteChange,
} from "./situations";

export type ComputeDSIProjectionInput = {
  subject_id: string;
  care_context: DementiaCareContext;
  cognitive_observations: readonly CognitiveObservation[];
  confusion_observations: readonly ConfusionObservation[];
  behavioral_observations: readonly BehavioralObservation[];
  functional_observations: readonly FunctionalObservation[];
  safety_observations: readonly SafetyObservation[];
  absent_observations?: readonly AbsentObservation[];
  functional_changes?: readonly FunctionalChange[];
  source_disagreements?: readonly SourceDisagreement[];
};

export type ComputeDSIProjectionResult = {
  patterns: Pattern[];
  care_relevant_situations: CareRelevantSituation[];
  projection: DSIProjection;
};

/**
 * Top-level entry point. Runs pattern detection, situation synthesis,
 * and assembles the DSIProjection read-model.
 *
 * Never diagnoses, never stages, never infers subtype. The output is
 * a structurally firewall-safe projection.
 */
export function computeDSIProjection(
  input: ComputeDSIProjectionInput,
): ComputeDSIProjectionResult {
  const functionalChanges: FunctionalChange[] = input.functional_changes ? [...input.functional_changes] : [];

  // Patterns
  const rq = detectRepeatedQuestionPattern({
    pattern_id: `pat_rq_${input.subject_id}`,
    subject_id: input.subject_id,
    cognitive_observations: input.cognitive_observations,
  });
  const ce = detectConfusionEpisodePattern({
    pattern_id: `pat_ce_${input.subject_id}`,
    subject_id: input.subject_id,
    confusion_observations: input.confusion_observations,
  });
  const bp = detectBehaviorPattern({
    pattern_id: `pat_beh_${input.subject_id}`,
    subject_id: input.subject_id,
    behavioral_observations: input.behavioral_observations,
  });
  const sp = detectSafetyPattern({
    pattern_id: `pat_safe_${input.subject_id}`,
    subject_id: input.subject_id,
    safety_observations: input.safety_observations,
  });
  const fp = detectFunctionalPattern({
    pattern_id: `pat_func_${input.subject_id}`,
    subject_id: input.subject_id,
    functional_observations: input.functional_observations,
  });

  const patterns: Pattern[] = [
    ...(rq.pattern ? [rq.pattern] : []),
    ...(ce.pattern ? [ce.pattern] : []),
    ...(bp ? [bp] : []),
    ...(sp ? [sp] : []),
    ...(fp ? [fp] : []),
  ];

  const hasAcute = detectAcuteChange({
    cognitive_observations: input.cognitive_observations,
    confusion_observations: input.confusion_observations,
    safety_observations: input.safety_observations,
  });

  const situation = synthesizeCareRelevantSituation({
    subject_id: input.subject_id,
    care_context: input.care_context,
    cognitive_observations: input.cognitive_observations,
    behavioral_observations: input.behavioral_observations,
    functional_observations: input.functional_observations,
    safety_observations: input.safety_observations,
    functional_changes: functionalChanges,
    patterns,
    has_acute_event: hasAcute,
  });

  const careRelevantSituations: CareRelevantSituation[] = situation ? [situation] : [];

  const projection: DSIProjection = DSIProjectionSchema.parse({
    subject_id: input.subject_id,
    care_context: input.care_context,
    cognitive_observations: input.cognitive_observations,
    confusion_observations: input.confusion_observations,
    behavioral_observations: input.behavioral_observations,
    functional_observations: input.functional_observations,
    safety_observations: input.safety_observations,
    absent_observations: input.absent_observations ?? [],
    functional_changes: functionalChanges,
    patterns,
    care_relevant_situations: careRelevantSituations,
    source_disagreements: input.source_disagreements ?? [],
    synthesized_at: new Date().toISOString(),
  });

  return {
    patterns,
    care_relevant_situations: careRelevantSituations,
    projection,
  };
}
