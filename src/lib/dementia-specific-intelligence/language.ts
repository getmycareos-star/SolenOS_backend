/**
 * Language Safety — vocabulary used (and avoided) in care-relevant
 * situation synthesis.
 *
 * Every emitted string in DSI goes through the qualification firewall
 * (./qualification-firewall.ts). The functions in this module are the
 * ONLY place where situation labels and descriptions are produced. They
 * build claims from a constrained vocabulary, then the caller asserts
 * via `assertClaimAllowed`.
 *
 * Safe language examples:
 *   - "Care-relevant situation: observations across cognition, function, and safety."
 *   - "Functional consequence: medication_self_management, prompting → supervised."
 *   - "Acute change flagged; recommend clinician attention."
 *
 * Unsafe language examples (NEVER produced):
 *   - "Her dementia is worsening."
 *   - "This is Alzheimer's disease."
 *   - "She is now moderately demented."
 *   - "She is no longer safe to live independently."
 */

import type { ContextStrength, ObservationDomain } from "./types";

// ─── Safe situation label builder ─────────────────────────────────────────

export type SituationLabelInput = {
  domains: readonly ObservationDomain[];
  cross_domain: boolean;
  has_functional_consequence: boolean;
  has_safety_consequence: boolean;
  acute_change_flag: boolean;
  context_strength: ContextStrength;
};

const DOMAIN_PHRASE: Record<ObservationDomain, string> = {
  cognition: "cognition",
  behavior: "behavior",
  function: "function",
  safety: "safety",
};

/**
 * Build a SAFE care-relevant situation label. The label travels with
 * the situation object; downstream emitters must call
 * `assertClaimAllowed` on the result before display.
 */
export function synthesizeSafeSituationLabel(input: SituationLabelInput): string {
  const parts: string[] = [];
  if (input.acute_change_flag) {
    parts.push("Acute change flagged");
  }
  if (input.cross_domain) {
    const domainList = input.domains.map((d) => DOMAIN_PHRASE[d]).join(", ");
    parts.push(`observations across ${domainList}`);
  } else if (input.domains.length === 1) {
    parts.push(`${DOMAIN_PHRASE[input.domains[0]]} observation(s) with care consequence`);
  } else {
    parts.push("observations with care consequence");
  }
  if (input.has_functional_consequence) {
    parts.push("functional consequence documented");
  }
  if (input.has_safety_consequence) {
    parts.push("safety consequence documented");
  }
  parts.push(`(context: ${input.context_strength})`);
  return parts.join(" — ");
}

// ─── Safe situation description builder ───────────────────────────────────

export type SituationDescriptionInput = SituationLabelInput & {
  care_relevance_tier: "low" | "medium" | "high";
};

/**
 * Build a SAFE multi-sentence description of a care-relevant situation.
 * The description is observation-grounded. It is NEVER a diagnosis.
 */
export function synthesizeSafeSituationDescription(input: SituationDescriptionInput): string {
  const sentences: string[] = [];
  sentences.push(
    `Care-relevant situation (${input.care_relevance_tier}) at context-strength ${input.context_strength}.`,
  );
  if (input.cross_domain) {
    const domainList = input.domains.map((d) => DOMAIN_PHRASE[d]).join(", ");
    sentences.push(
      `Observations span multiple care domains: ${domainList}.`,
    );
  } else if (input.domains.length === 1) {
    sentences.push(`Domain in scope: ${DOMAIN_PHRASE[input.domains[0]]}.`);
  }
  if (input.has_functional_consequence) {
    sentences.push("A functional consequence is documented.");
  }
  if (input.has_safety_consequence) {
    sentences.push("A safety consequence is documented.");
  }
  if (input.acute_change_flag) {
    sentences.push("An acute change is flagged for attention.");
  }
  sentences.push("Clinical interpretation remains with the care team.");
  return sentences.join(" ");
}

// ─── Allowed phrase catalog (used in verify script) ──────────────────────

/**
 * Phrases the system is allowed to produce. Used by the verify script
 * to ensure emitted claims are catalog-safe.
 */
export const ALLOWED_PHRASE_PATTERNS: readonly RegExp[] = [
  /\bcare[- ]relevant\b/i,
  /\bfunctional consequence\b/i,
  /\bsafety consequence\b/i,
  /\bcontext strength\b/i,
  /\bacute change (?:flagged|flag)\b/i,
  /\bobservations? (?:across|in|spanning)\b/i,
  /\bclinical interpretation remains with the care team\b/i,
  /\bdoc(?:umented|umentation) (?:shows?|indicates?)\b/i,
  /\bno care[- ]relevance threshold met\b/i,
] as const;

/**
 * Phrases the system must NEVER produce. Subset of
 * FORBIDDEN_CLAIM_PATTERNS in qualification-firewall.ts but expressed
 * at the phrase level for verification of synthesis output.
 */
export const FORBIDDEN_PHRASE_PATTERNS: readonly RegExp[] = [
  /\b(?:her|his|their|mom|dad|patient)\s+(?:has|have)\s+(?:dementia|alzheimer(?:'s)?|lewy|frontotemporal|vascular)\b/i,
  /\b(?:her|his|their)\s+(?:dementia|alzheimer(?:'s)?|disease)\s+(?:is|has|is\s+now)\s+(?:progressing|worsening|advancing|declining)\b/i,
  /\b(?:early|middle|late|severe|moderate|mild)\s*[- ]?\s*stage\b/i,
  /\b(?:stage\s+(?:early|middle|mid|late|severe|moderate|mild|1|2|3|4|5|6|7))\b/i,
  /\b(?:is|are)\s+(?:no longer\s+)?(?:safe|unsafe)\s+to\s+live\s+alone\b/i,
  /\b(?:unsafe to|incapable of|no longer able to) (?:live|drive|manage)\b/i,
  /\brecommend(?:ed|ation)?\s+(?:hiring|placing|revoking|starting|prescribing)\b/i,
  /\bshort[- ]?term memory is (?:deteriorating|declining|worsening|failing)\b/i,
  /\bcognitive (?:function|ability) is (?:declining|deteriorating|worsening)\b/i,
  /\bthis is (?:alzheimer(?:'s)?|dementia|lewy|frontotemporal|vascular)\b/i,
  /\bsymptoms? (?:are consistent with|suggest|indicate) (?:alzheimer(?:'s)?|lewy|frontotemporal|vascular)\b/i,
  /\bdelirium\b/i,
  /\bsundowning syndrome\b/i,
  /\baphasia\b/i,
] as const;

/**
 * Check whether a phrase is allowed (no forbidden pattern matches).
 * Used by the verify script to assert synthesis output is safe.
 */
export function isAllowedPhrase(phrase: string): boolean {
  return !FORBIDDEN_PHRASE_PATTERNS.some((p) => p.test(phrase));
}
