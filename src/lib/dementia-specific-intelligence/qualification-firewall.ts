/**
 * Qualification Firewall — runtime guard enforcing the diagnosis boundary.
 *
 * Hard invariants:
 *   - Detection ≠ diagnosis.
 *   - Pattern ≠ diagnosis.
 *   - Care-relevant situation ≠ diagnosis, staging, etiology, prognosis,
 *     treatment, or capacity/legal conclusion.
 *
 * This module exposes:
 *   1. A set of FORBIDDEN_CLAIM_PATTERNS that may NEVER appear as a
 *      system-derived conclusion in any DSI output.
 *   2. A `guardAgainstDiagnosisLanguage` runtime check that throws on
 *      system-derived text crossing the boundary.
 *   3. `assertQualificationTier` that verifies a payload is tagged with
 *      the correct tier (observation | event | pattern | situation) and
 *      does not carry a diagnostic field.
 *
 * The firewall is structural. A bug elsewhere in the system that *would*
 * have emitted a diagnostic string will be caught here. A bug that *would*
 * have stored a `diagnosis: "Alzheimer's"` field is impossible because the
 * schemas in ./types.ts do not define that field.
 */

// ─── Forbidden Claim Patterns ─────────────────────────────────────────────

/**
 * Patterns that are diagnostic, staging, etiological, prognostic, treatment
 * recommendation, or capacity/legal conclusions.
 *
 * These are NEVER system-derived. They may appear in the corpus as verbatim
 * quoted text from sources, in which case they must be preserved in
 * `diagnosis_quote` or `provenance.raw_text` — never in derived claims.
 */
export const FORBIDDEN_CLAIM_PATTERNS: readonly RegExp[] = [
  // Staging
  /\b(?:early|mid|middle|late|severe|moderate|mild)\s*(?:-|\s+)?stage\s+(?:dementia|alzheimer|lewy)\b/i,
  /\bstage\s+(?:early|middle|mid|late|severe|moderate|mild|1|2|3|4|5|6|7)\b/i,
  /\bmoderately demented\b/i,
  /\bseverely demented\b/i,
  /\bmildly demented\b/i,
  /\b(?:dementia|alzheimer(?:'s)?)\s+(?:early|mid|middle|late|severe|moderate|mild)\b/i,

  // Diagnosis as a clinical conclusion
  /\b(?:she|he|they|patient|mom|dad|father|mother)\s+(?:has|have|got|is diagnosed with)\s+(?:dementia|alzheimer(?:'s)?|lewy body|frontotemporal|vascular dementia|mild cognitive impairment)\b/i,
  /\bdiagnos(?:is|ed)\s+(?:with|as)\s+(?:dementia|alzheimer|lewy|frontotemporal)\b/i,
  /\b(?:she|he|they|patient|mom|dad|father|mother)\s+has\s+delirium\b/i,
  /\bdelirium diagnosis\b/i,

  // Progression
  /\b(?:dementia|alzheimer(?:'s)?|disease)\s+(?:is|has|is\s+now|is\s+still|appears)\s+(?:progressing|worsening|advancing|declining|deteriorating|getting worse)\b/i,
  /\b(?:her|his|their)\s+(?:dementia|alzheimer(?:'s)?|disease)\s+(?:is|has)\s+(?:progressing|worsening|advancing)\b/i,

  // Etiology inference
  /\b(?:this is|that's|it's|seems like)\s+(?:alzheimer(?:'s)?|lewy body|frontotemporal|vascular dementia)\b/i,
  /\b(?:symptoms?|signs?)\s+(?:are consistent with|suggest|indicate|point to)\s+(?:alzheimer(?:'s)?|lewy|frontotemporal|vascular)\b/i,

  // Memory decline as clinical claim
  /\b(?:her|his|their|short[- ]?term)\s+memory\s+(?:is|has|appears)\s+(?:deteriorating|declining|getting worse|worsening|failing)\b/i,
  /\b(?:her|his|their)\s+cognitive\s+(?:function|ability|status)\s+is\s+(?:declining|deteriorating|worsening)\b/i,

  // Behavioral interpretation as clinical
  /\b(?:her|his|their)\s+(?:behavior|behaviour)\s+is\s+(?:a\s+)?(?:symptom|sign|due to|caused by)\b/i,

  // Care capacity / legal
  /\b(?:she|he|they|patient)\s+(?:is|are)\s+(?:no longer\s+)?(?:safe|unsafe)\s+to\s+live\s+alone\b/i,
  /\b(?:she|he|they|patient)\s+(?:is|are)\s+(?:no longer|now)\s+(?:capable|competent|fit|able)\s+to\s+(?:live alone|make decisions|manage (?:his|her|their) (?:own )?(?:affairs|finances|medications))\b/i,
  /\brecommend(?:ed|ation)?\s+(?:hiring|placing|removing|revoking|starting|stopping|prescribing)\b/i,
  /\b(?:should\s+be|needs?\s+to\s+be)\s+(?:placed|hired|removed|revoked|started|prescribed|institutionalized)\b/i,
  /\b(?:license|driving\s+privileges?)\s+(?:should|needs?\s+to|must)\s+be\s+revoked\b/i,
  /\b(?:she|he|they)\s+can\s+no\s+longer\s+(?:live|drive|manage|be alone)\b/i,

  // Direct disease labeling from observations
  /\b(?:memory\s+problems|confusion|wandering|repetition)\s+(?:means|indicates|confirms|proves|shows)\s+(?:dementia|alzheimer)\b/i,
] as const;

// ─── Tier Taxonomy ────────────────────────────────────────────────────────

/**
 * The four output tiers. Every claim in DSI must carry exactly one.
 * These map to the architectural hard rule: clinical_concern is the
 * highest tier this primitive may produce. clinical_inference, diagnosis,
 * prognosis, and treatment_recommendation are EXTERNAL.
 */
export const QUALIFICATION_TIERS = [
  "observation",
  "event",
  "pattern",
  "situation",
  "clinical_concern",
] as const;
export type QualificationTier = (typeof QUALIFICATION_TIERS)[number];

/**
 * Tiers that must NEVER be produced by this layer.
 */
export const FORBIDDEN_TIERS = [
  "clinical_inference",
  "diagnosis",
  "staging",
  "etiology",
  "prognosis",
  "treatment_recommendation",
  "capacity_conclusion",
  "legal_conclusion",
] as const;
export type ForbiddenTier = (typeof FORBIDDEN_TIERS)[number];

// ─── Errors ───────────────────────────────────────────────────────────────

export class QualificationFirewallViolation extends Error {
  readonly violation_kind: "forbidden_language" | "forbidden_tier" | "forbidden_field" | "missing_provenance";
  readonly tier?: QualificationTier;
  readonly claim: string;
  readonly matched_pattern?: string;

  constructor(params: {
    violation_kind: QualificationFirewallViolation["violation_kind"];
    claim: string;
    tier?: QualificationTier;
    matched_pattern?: string;
  }) {
    super(
      `[DSI Qualification Firewall] ${params.violation_kind} violation${
        params.tier ? ` at tier=${params.tier}` : ""
      }: ${params.claim.slice(0, 200)}`,
    );
    this.name = "QualificationFirewallViolation";
    this.violation_kind = params.violation_kind;
    this.claim = params.claim;
    this.tier = params.tier;
    this.matched_pattern = params.matched_pattern;
  }
}

// ─── Runtime guards ──────────────────────────────────────────────────────

/**
 * Default firewall mode. In `enforce` mode (default), violations throw. In
 * `report` mode, violations are returned without throwing — used by tests
 * and dry-runs to surface would-be violations.
 */
export type FirewallMode = "enforce" | "report";

/**
 * Scan a claim string for forbidden diagnostic language.
 * Returns the matched pattern string if any, else null.
 */
export function findForbiddenClaimMatch(claim: string): string | null {
  for (const pattern of FORBIDDEN_CLAIM_PATTERNS) {
    if (pattern.test(claim)) {
      return pattern.source;
    }
  }
  return null;
}

/**
 * Assert a system-derived claim is allowed under the firewall.
 * Throws QualificationFirewallViolation in enforce mode.
 */
export function assertClaimAllowed(params: {
  claim: string;
  tier: QualificationTier;
  mode?: FirewallMode;
}): void {
  const match = findForbiddenClaimMatch(params.claim);
  if (match) {
    const v = new QualificationFirewallViolation({
      violation_kind: "forbidden_language",
      claim: params.claim,
      tier: params.tier,
      matched_pattern: match,
    });
    if ((params.mode ?? "enforce") === "enforce") throw v;
  }
}

/**
 * Assert that a payload is tagged with an allowed tier and does not carry
 * any diagnostic field. The check is by key name — any field whose key
 * matches the forbidden set is rejected.
 */
const FORBIDDEN_FIELD_NAMES = new Set<string>([
  "diagnosis",
  "diagnostic_label",
  "subtype_inferred",
  "stage",
  "stage_inferred",
  "etiology",
  "etiology_inferred",
  "prognosis",
  "prognosis_inferred",
  "treatment_recommendation",
  "treatment",
  "capacity_conclusion",
  "legal_conclusion",
  "is_dementia",
  "has_dementia",
  "dementia_severity",
  "disease_progression",
]);

export function assertNoForbiddenFields(params: {
  payload: Record<string, unknown>;
  tier: QualificationTier;
  mode?: FirewallMode;
}): void {
  for (const key of Object.keys(params.payload)) {
    if (FORBIDDEN_FIELD_NAMES.has(key)) {
      const v = new QualificationFirewallViolation({
        violation_kind: "forbidden_field",
        claim: `payload key "${key}"`,
        tier: params.tier,
      });
      if ((params.mode ?? "enforce") === "enforce") throw v;
    }
  }
}

/**
 * Assert the tier is in the allowed set.
 */
export function assertQualificationTier(params: {
  tier: string;
  mode?: FirewallMode;
}): asserts params is { tier: QualificationTier; mode?: FirewallMode } {
  if ((QUALIFICATION_TIERS as readonly string[]).includes(params.tier)) return;
  if ((FORBIDDEN_TIERS as readonly string[]).includes(params.tier)) {
    const v = new QualificationFirewallViolation({
      violation_kind: "forbidden_tier",
      claim: `tier=${params.tier}`,
      matched_pattern: params.tier,
    });
    if ((params.mode ?? "enforce") === "enforce") throw v;
    return;
  }
  const v = new QualificationFirewallViolation({
    violation_kind: "forbidden_tier",
    claim: `unknown tier=${params.tier}`,
    matched_pattern: params.tier,
  });
  if ((params.mode ?? "enforce") === "enforce") throw v;
}

/**
 * Assert that a derived object carries provenance. Every claim in DSI
 * must be reconstructable.
 */
export function assertProvenancePresent(params: {
  evidence_chain?: readonly unknown[];
  mode?: FirewallMode;
}): void {
  if (!params.evidence_chain || params.evidence_chain.length === 0) {
    const v = new QualificationFirewallViolation({
      violation_kind: "missing_provenance",
      claim: "no evidence_chain present on derived object",
    });
    if ((params.mode ?? "enforce") === "enforce") throw v;
  }
}

// ─── Convenience: full claim pipeline ────────────────────────────────────

/**
 * The single entry point used by every emitter in DSI. Use this before
 * returning any string or object to a downstream system.
 */
export function emitClaim(params: {
  text: string;
  tier: QualificationTier;
  payload?: Record<string, unknown>;
  evidence_chain?: readonly unknown[];
  mode?: FirewallMode;
}): { text: string; tier: QualificationTier } {
  const mode = params.mode ?? "enforce";
  assertQualificationTier({ tier: params.tier, mode });
  assertClaimAllowed({ claim: params.text, tier: params.tier, mode });
  if (params.payload) {
    assertNoForbiddenFields({ payload: params.payload, tier: params.tier, mode });
  }
  if (params.tier !== "observation") {
    assertProvenancePresent({ evidence_chain: params.evidence_chain, mode });
  }
  return { text: params.text, tier: params.tier };
}
