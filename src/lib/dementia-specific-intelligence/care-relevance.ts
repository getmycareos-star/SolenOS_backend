/**
 * Care-Relevance Classifier — the cross-domain synthesis that makes
 * Dementia-Specific Intelligence distinct from generic change detection.
 *
 * The classifier answers: "Is this combination of observations care-relevant
 * in a dementia-care context, and at what tier?"
 *
 * Care-relevance requires EITHER:
 *   (a) a functional consequence, OR
 *   (b) a safety consequence, OR
 *   (c) an established context + cross-domain pattern.
 *
 * Critically, care-relevance is NOT:
 *   - The number of observations (symptom count).
 *   - The acuteness of a single event.
 *   - The interpretation of the observation.
 *
 * Symptom count alone never establishes care-relevance. Cross-domain
 * convergence (e.g., memory + medication + function) does.
 */

import type {
  CareRelevantSituation,
  ContextStrength,
  CognitiveObservation,
  BehavioralObservation,
  FunctionalObservation,
  FunctionalChange,
  SafetyObservation,
  Pattern,
  Provenance,
  ObservationDomain,
  ConcernStrength,
} from "./types";
import { isDementiaContextActive } from "./context";

// ─── Care-relevance scoring ────────────────────────────────────────────────

export type CareRelevanceInput = {
  context_strength: ContextStrength;
  /** Observations across domains */
  cognitive_observations: readonly CognitiveObservation[];
  behavioral_observations: readonly BehavioralObservation[];
  functional_observations: readonly FunctionalObservation[];
  safety_observations: readonly SafetyObservation[];
  functional_changes: readonly FunctionalChange[];
  patterns: readonly Pattern[];
  /** Whether at least one acute event is present */
  has_acute_event: boolean;
};

export type CareRelevanceAssessment = {
  /** Whether the situation is care-relevant at all */
  care_relevant: boolean;
  /** Care-relevance tier */
  tier: "low" | "medium" | "high";
  /** Domains involved */
  domains_involved: ObservationDomain[];
  /** Whether this spans more than one domain */
  cross_domain: boolean;
  /** Whether a functional consequence was observed */
  has_functional_consequence: boolean;
  /** Whether a safety consequence was observed */
  has_safety_consequence: boolean;
  /** Whether a context-appropriate interpretation is permitted */
  context_active: boolean;
  /** Acute change flag (always preserved; never interpreted) */
  acute_change_flag: boolean;
};

/**
 * Assess care-relevance from observations, patterns, and context.
 *
 * The assessment is observation-only. It never concludes diagnosis.
 */
export function assessCareRelevance(input: CareRelevanceInput): CareRelevanceAssessment {
  const contextActive = isDementiaContextActive(input.context_strength);

  // Domain presence
  const domains = new Set<ObservationDomain>();
  if (input.cognitive_observations.length > 0) domains.add("cognition");
  if (input.behavioral_observations.length > 0) domains.add("behavior");
  if (input.functional_observations.length > 0) domains.add("function");
  if (input.safety_observations.length > 0) domains.add("safety");
  const domainsArr = Array.from(domains);
  const crossDomain = domainsArr.length >= 2;

  // Functional consequence: any functional change with care_relevant=true
  // OR any functional observation with non-independent level
  const hasFunctionalConsequence =
    input.functional_changes.some((c) => c.care_relevant) ||
    input.functional_observations.some(
      (o) => o.observed_independence !== "independent" && o.observed_independence !== "unknown",
    );

  // Safety consequence: any safety observation with non-unknown care_consequence
  const hasSafetyConsequence = input.safety_observations.some(
    (o) => o.care_consequence !== "none" && o.care_consequence !== "unknown",
  );

  // Cross-domain pattern: a pattern that spans >=2 domains
  const hasCrossDomainPattern = input.patterns.some(
    (p) => p.temporal_class === "recurring" || p.temporal_class === "chronic",
  );

  // Tier rules
  let tier: "low" | "medium" | "high" = "low";
  let careRelevant = false;

  if (hasSafetyConsequence && contextActive) {
    careRelevant = true;
    tier = "high";
  } else if (hasFunctionalConsequence && contextActive) {
    careRelevant = true;
    tier = "high";
  } else if (crossDomain && hasCrossDomainPattern && contextActive) {
    careRelevant = true;
    tier = "medium";
  } else if (crossDomain && hasCrossDomainPattern && input.context_strength === "concern_only") {
    careRelevant = true;
    tier = "low";
  } else if (input.has_acute_event && hasSafetyConsequence) {
    // Acute change is *always* a care-relevant flag (for downstream attention)
    // even without a strong context, because it is acute — but tier remains low.
    careRelevant = true;
    tier = "low";
  }

  return {
    care_relevant: careRelevant,
    tier,
    domains_involved: domainsArr,
    cross_domain: crossDomain,
    has_functional_consequence: hasFunctionalConsequence,
    has_safety_consequence: hasSafetyConsequence,
    context_active: contextActive,
    acute_change_flag: input.has_acute_event,
  };
}

// ─── Safe language for care-relevance claims ──────────────────────────────

/**
 * Generate a SAFE care-relevance claim string. This is a SYSTEM-EMITTED
 * claim that goes through the qualification firewall (asserted by the
 * caller via `assertClaimAllowed`).
 */
export function buildCareRelevanceClaim(assessment: CareRelevanceAssessment): string {
  if (!assessment.care_relevant) {
    return "Observations recorded; no care-relevance threshold met at this time.";
  }
  const parts: string[] = [];
  if (assessment.cross_domain) {
    parts.push("observations span multiple care domains");
  } else {
    parts.push(`observations in ${assessment.domains_involved.join(", ")}`);
  }
  if (assessment.has_functional_consequence) {
    parts.push("a functional consequence is documented");
  }
  if (assessment.has_safety_consequence) {
    parts.push("a safety consequence is documented");
  }
  if (assessment.acute_change_flag) {
    parts.push("an acute change is flagged for attention");
  }
  return `Care-relevant (${assessment.tier}): ${parts.join("; ")}.`;
}

// ─── Source agreement ─────────────────────────────────────────────────────

export type SourceAgreementInput = {
  topic: string;
  observations: ReadonlyArray<{
    observation_id: string;
    source_id: string;
    concern_strength: ConcernStrength;
  }>;
};

/**
 * Compute the source agreement on a topic. Returns disagreement if
 * observations diverge in concern_strength. The result is *surfaced*,
 * never silently resolved.
 */
export function computeSourceAgreement(input: SourceAgreementInput): {
  agreement: "consistent" | "mixed" | "conflicting";
  conflicting_observation_ids: string[];
} {
  const bySource = new Map<string, ConcernStrength>();
  for (const obs of input.observations) {
    const existing = bySource.get(obs.source_id);
    if (existing && existing !== obs.concern_strength) {
      return {
        agreement: "conflicting",
        conflicting_observation_ids: input.observations.map((o) => o.observation_id),
      };
    }
    bySource.set(obs.source_id, obs.concern_strength);
  }
  const strengths = new Set(bySource.values());
  if (strengths.size === 1) return { agreement: "consistent", conflicting_observation_ids: [] };
  if (strengths.size === 2 && strengths.has("vague_concern")) {
    return { agreement: "mixed", conflicting_observation_ids: [] };
  }
  return { agreement: "mixed", conflicting_observation_ids: [] };
}

// ─── Provenance chain assembly ────────────────────────────────────────────

/**
 * Build a deduplicated evidence chain from any list of observations.
 * Order is preserved as input.
 */
export function buildEvidenceChain(
  observations: ReadonlyArray<{ provenance: Provenance }>,
): Provenance[] {
  const seen = new Set<string>();
  const out: Provenance[] = [];
  for (const obs of observations) {
    const key = `${obs.provenance.source_type}|${obs.provenance.observer_id ?? ""}|${obs.provenance.observed_at ?? ""}|${obs.provenance.raw_text.slice(0, 40)}`;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(obs.provenance);
  }
  return out;
}

// ─── Care-relevant situation shape (used by ./situations.ts) ──────────────

/**
 * Build a care-relevant situation shape. The actual CareRelevantSituation
 * is created in ./situations.ts, which uses this for shape decisions.
 */
export type CareRelevantSituationShape = Pick<
  CareRelevantSituation,
  | "domains"
  | "cross_domain"
  | "functional_consequences"
  | "safety_consequences"
  | "care_relevance"
  | "context_strength"
  | "acute_change_flag"
  | "pending_evaluation"
>;
