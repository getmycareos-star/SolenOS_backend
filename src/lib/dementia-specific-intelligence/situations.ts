/**
 * Care-Relevant Situation synthesis.
 *
 * A "situation" is the cross-domain care-relevant synthesis unit. It may
 * be a single event with care consequence OR a pattern of cross-domain
 * observations. A situation REQUIRES at least one of:
 *   - functional consequence
 *   - safety consequence
 *   - cross-domain pattern with care context
 *
 * Every situation is fully reconstructable from underlying observations.
 * No situation is allowed to carry diagnosis, stage, etiology, prognosis,
 * treatment, or capacity/legal conclusions.
 *
 * The synthesis language is constrained by ./language.ts.
 */

import type {
  CareRelevantSituation,
  CognitiveObservation,
  BehavioralObservation,
  FunctionalObservation,
  SafetyObservation,
  FunctionalChange,
  Pattern,
  DementiaCareContext,
  ContextStrength,
  Provenance,
  ObservationDomain,
} from "./types";
import { assessCareRelevance, buildEvidenceChain } from "./care-relevance";
import {
  synthesizeSafeSituationLabel,
  synthesizeSafeSituationDescription,
} from "./language";

// ─── Synthesis ────────────────────────────────────────────────────────────

let situationCounter = 0;
function newSituationId(): string {
  situationCounter++;
  return `sit_${Date.now()}_${situationCounter.toString(36)}`;
}

export type BuildSituationInput = {
  subject_id: string;
  care_context: DementiaCareContext;
  cognitive_observations: readonly CognitiveObservation[];
  behavioral_observations: readonly BehavioralObservation[];
  functional_observations: readonly FunctionalObservation[];
  safety_observations: readonly SafetyObservation[];
  functional_changes: readonly FunctionalChange[];
  patterns: readonly Pattern[];
  /** Whether at least one acute event is present */
  has_acute_event: boolean;
};

/**
 * Synthesize a care-relevant situation from observations, patterns, and
 * context. Returns `null` if no care-relevant situation exists.
 */
export function synthesizeCareRelevantSituation(
  input: BuildSituationInput,
): CareRelevantSituation | null {
  const assessment = assessCareRelevance({
    context_strength: input.care_context.context_strength,
    cognitive_observations: input.cognitive_observations,
    behavioral_observations: input.behavioral_observations,
    functional_observations: input.functional_observations,
    safety_observations: input.safety_observations,
    functional_changes: input.functional_changes,
    patterns: input.patterns,
    has_acute_event: input.has_acute_event,
  });

  if (!assessment.care_relevant) return null;

  // Component IDs — observations and patterns
  const componentObservationIds = [
    ...input.cognitive_observations.map((o) => o.observation_id),
    ...input.behavioral_observations.map((o) => o.observation_id),
    ...input.functional_observations.map((o) => o.observation_id),
    ...input.safety_observations.map((o) => o.observation_id),
  ];
  const componentPatternIds = input.patterns.map((p) => p.pattern_id);

  // Functional consequences: list activities with non-independent current level
  const functional_consequences = input.functional_changes
    .filter((c) => c.care_relevant)
    .map((c) => `${c.activity}: ${c.baseline.independence} → ${c.current.independence}`);

  // Safety consequences: distinct observed care_consequence values
  const safety_consequences = Array.from(
    new Set(
      input.safety_observations
        .filter((s) => s.care_consequence !== "none" && s.care_consequence !== "unknown")
        .map((s) => `${s.observation_type}: ${s.care_consequence}`),
    ),
  );

  // Evidence chain
  const evidence_chain = buildEvidenceChain([
    ...input.cognitive_observations,
    ...input.behavioral_observations,
    ...input.functional_observations,
    ...input.safety_observations,
  ]);

  // Label and description — built via language module, which constrains output
  const domainsList: ObservationDomain[] = assessment.domains_involved;
  const label = synthesizeSafeSituationLabel({
    domains: domainsList,
    cross_domain: assessment.cross_domain,
    has_functional_consequence: assessment.has_functional_consequence,
    has_safety_consequence: assessment.has_safety_consequence,
    acute_change_flag: assessment.acute_change_flag,
    context_strength: input.care_context.context_strength,
  });
  const _description = synthesizeSafeSituationDescription({
    domains: domainsList,
    cross_domain: assessment.cross_domain,
    has_functional_consequence: assessment.has_functional_consequence,
    has_safety_consequence: assessment.has_safety_consequence,
    acute_change_flag: assessment.acute_change_flag,
    context_strength: input.care_context.context_strength,
    care_relevance_tier: assessment.tier,
  });
  // description kept in synthesis for downstream use; not stored on the
  // situation object (the situation label is what travels).
  void _description;

  const situation: CareRelevantSituation = {
    situation_id: newSituationId(),
    subject_id: input.subject_id,
    situation_label: label,
    component_observation_ids: componentObservationIds,
    component_pattern_ids: componentPatternIds,
    domains: domainsList,
    cross_domain: assessment.cross_domain,
    functional_consequences,
    safety_consequences,
    care_relevance: assessment.tier,
    context_strength: input.care_context.context_strength,
    acute_change_flag: assessment.acute_change_flag,
    pending_evaluation:
      input.care_context.pending_evaluation &&
      input.care_context.context_strength !== "established",
    evidence_chain,
    synthesized_at: new Date().toISOString(),
  };

  return situation;
}

// ─── Multiple-situation synthesis ─────────────────────────────────────────

/**
 * Synthesize situations across a window. A single subject can have
 * multiple care-relevant situations. Each is independent and
 * reconstructable.
 */
export function synthesizeAllCareRelevantSituations(
  inputs: BuildSituationInput[],
): CareRelevantSituation[] {
  const out: CareRelevantSituation[] = [];
  for (const input of inputs) {
    const s = synthesizeCareRelevantSituation(input);
    if (s) out.push(s);
  }
  return out;
}

// ─── Conflict surfacing ──────────────────────────────────────────────────

/**
 * Build a SourceDisagreement object from conflicting observations on a
 * topic. The disagreement is SURFACED, not resolved. Resolution belongs
 * to the upstream Contradiction Intelligence.
 */
export function surfaceSourceDisagreement(params: {
  disagreement_id: string;
  subject_id: string;
  topic: string;
  observations: ReadonlyArray<{
    observation_id: string;
    source_id: string;
    source_type: import("./types").SourceType;
  }>;
}): import("./types").SourceDisagreement {
  return {
    disagreement_id: params.disagreement_id,
    subject_id: params.subject_id,
    topic: params.topic,
    conflicting_observations: params.observations.map((o) => o.observation_id),
    source_breakdown: params.observations.map((o) => ({
      source_id: o.source_id,
      source_type: o.source_type,
      observation_id: o.observation_id,
    })),
    surfaced_at: new Date().toISOString(),
  };
}

// ─── Acute change flag builder ────────────────────────────────────────────

/**
 * Determine whether a subject's recent observations include an acute
 * change. This is observation-only; interpretation is left to downstream
 * clinical care. The flag is set when:
 *   - The most recent observation of a domain is acute.
 *   - OR a confusion observation has onset=acute.
 */
export function detectAcuteChange(params: {
  cognitive_observations: readonly CognitiveObservation[];
  confusion_observations: ReadonlyArray<{ attributes: { onset: string } }>;
  safety_observations: readonly SafetyObservation[];
}): boolean {
  for (const o of params.cognitive_observations) {
    if (o.quantifier?.window_start) {
      // Quantified within a short window may indicate acute clustering;
      // do not assert it is acute.
    }
  }
  for (const c of params.confusion_observations) {
    if (c.attributes.onset === "acute") return true;
  }
  for (const s of params.safety_observations) {
    if (s.temporal_class === "acute") return true;
  }
  return false;
}
