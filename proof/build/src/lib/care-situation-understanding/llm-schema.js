"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.LlmUnderstandingOutputSchema = exports.LlmPossibleLinkSchema = exports.LlmNonCareFactSchema = exports.LlmUnknownSchema = exports.LlmOutcomeSchema = exports.LlmDecisionSchema = exports.LlmEventSchema = exports.LlmObservationSchema = void 0;
exports.extractAllRawFragments = extractAllRawFragments;
exports.verifyRawFragments = verifyRawFragments;
exports.verifyField = verifyField;
exports.verifyAllFields = verifyAllFields;
exports.verifyCoverage = verifyCoverage;
exports.validateMedicalBoundary = validateMedicalBoundary;
/**
 * Zod schema for validating LLM Structured Understanding output.
 * Must validate against the same shape as CareRealityExtractionResult
 * to ensure downstream compatibility.
 */
const zod_1 = require("zod");
exports.LlmObservationSchema = zod_1.z.object({
    description: zod_1.z.string().min(1).max(500),
    approximate_time: zod_1.z.string().nullable(),
    confidence: zod_1.z.enum(["low", "medium", "high"]),
    raw_fragment: zod_1.z.string().min(1),
});
exports.LlmEventSchema = zod_1.z.object({
    description: zod_1.z.string().min(1).max(500),
    time: zod_1.z.string().nullable(),
    participants: zod_1.z.array(zod_1.z.string()).max(10),
    raw_fragment: zod_1.z.string().min(1),
});
exports.LlmDecisionSchema = zod_1.z.object({
    description: zod_1.z.string().min(1).max(500),
    who: zod_1.z.array(zod_1.z.string()).max(10),
    why: zod_1.z.string().nullable(),
    reason_unknown: zod_1.z.boolean(),
    status: zod_1.z.enum(["active", "completed", "changed", "reversed", "uncertain", "needs_review", "pending"]),
    raw_fragment: zod_1.z.string().min(1),
});
exports.LlmOutcomeSchema = zod_1.z.object({
    description: zod_1.z.string().min(1).max(500),
    status: zod_1.z.enum(["observed", "pending", "uncertain", "ongoing", "resolved", "changed"]),
    raw_fragment: zod_1.z.string().min(1),
});
exports.LlmUnknownSchema = zod_1.z.object({
    question: zod_1.z.string().min(1).max(500),
    status: zod_1.z.enum(["open", "answered", "declined", "no_longer_relevant"]),
    raw_fragment: zod_1.z.string().min(1),
});
exports.LlmNonCareFactSchema = zod_1.z.object({
    layer: zod_1.z.enum(["contributor_load", "disagreement_perspective"]),
    text: zod_1.z.string().min(1).max(500),
    raw_fragment: zod_1.z.string().min(1),
});
exports.LlmPossibleLinkSchema = zod_1.z.object({
    text: zod_1.z.string().min(1).max(500),
    causation_claimed: zod_1.z.literal(false, {
        message: "causation_claimed must always be false — never assert causation",
    }),
});
exports.LlmUnderstandingOutputSchema = zod_1.z.object({
    observations: zod_1.z.array(exports.LlmObservationSchema).max(20).default([]),
    events: zod_1.z.array(exports.LlmEventSchema).max(10).default([]),
    decisions: zod_1.z.array(exports.LlmDecisionSchema).max(10).default([]),
    outcomes: zod_1.z.array(exports.LlmOutcomeSchema).max(10).default([]),
    unknowns: zod_1.z.array(exports.LlmUnknownSchema).max(10).default([]),
    non_care_facts: zod_1.z.array(exports.LlmNonCareFactSchema).max(10).default([]),
    possible_links: zod_1.z.array(exports.LlmPossibleLinkSchema).max(10).default([]),
});
/**
 * All text fields that must be grounded in the original caregiver input.
 * Used by rawFragmentVerification to enforce no-invention.
 */
function extractAllRawFragments(output) {
    return [
        ...output.observations.map((o) => o.raw_fragment),
        ...output.events.map((e) => e.raw_fragment),
        ...output.decisions.map((d) => d.raw_fragment),
        ...output.outcomes.map((o) => o.raw_fragment),
        ...output.unknowns.map((u) => u.raw_fragment),
        ...output.non_care_facts.map((n) => n.raw_fragment),
    ];
}
/**
 * Verify that every raw_fragment is an exact substring of the original input.
 * This enforces the "No invented precision" boundary — the LLM must not
 * fabricate, paraphrase, or invent content not present in the caregiver's text.
 *
 * Returns { ok, failures } where failures lists each fragment that could not
 * be verified. On any failure, the caller should fall back to deterministic.
 */
function verifyRawFragments(output, originalInput) {
    const failures = [];
    const fragments = extractAllRawFragments(output);
    for (const fragment of fragments) {
        if (fragment.length === 0) {
            failures.push(`empty raw_fragment`);
            continue;
        }
        const normalizedInput = originalInput.trim();
        if (!normalizedInput.includes(fragment.trim())) {
            failures.push(`raw_fragment not found in input: "${fragment.slice(0, 80)}"`);
        }
    }
    return { ok: failures.length === 0, failures };
}
/**
 * Verify a SINGLE field of a SINGLE object against the original input.
 * Returns the specific failure so the model can be asked to fix just that field.
 *
 * Used by the self-verification loop: instead of discarding the whole output on
 * one bad field, we report the exact failures and retry only the broken fields.
 */
function verifyField(field, value, originalInput) {
    if (value === undefined || value === null) {
        return { ok: true };
    }
    if (typeof value !== "string") {
        return { ok: true };
    }
    if (value.trim().length === 0) {
        return { ok: false, failure: `${field} is empty` };
    }
    if (!originalInput.includes(value.trim())) {
        return {
            ok: false,
            failure: `${field} "${value.slice(0, 60)}..." is not an exact substring of the input`,
        };
    }
    return { ok: true };
}
/**
 * Verify every raw_fragment-bearing field in the output, returning a list of
 * specific, actionable failures suitable for injection into a retry prompt.
 */
function verifyAllFields(output, originalInput) {
    const failures = [];
    const checks = [
        ...output.observations.map((o) => ({ label: `observation.raw_fragment`, value: o.raw_fragment })),
        ...output.events.map((e) => ({ label: `event.raw_fragment`, value: e.raw_fragment })),
        ...output.decisions.map((d) => ({ label: `decision.raw_fragment`, value: d.raw_fragment })),
        ...output.outcomes.map((o) => ({ label: `outcome.raw_fragment`, value: o.raw_fragment })),
        ...output.unknowns.map((u) => ({ label: `unknown.raw_fragment`, value: u.raw_fragment })),
        ...output.non_care_facts.map((n) => ({ label: `non_care_fact.raw_fragment`, value: n.raw_fragment })),
    ];
    for (const { label, value } of checks) {
        const r = verifyField(label, value, originalInput);
        if (!r.ok && r.failure)
            failures.push(r.failure);
    }
    return { ok: failures.length === 0, failures };
}
/**
 * Cross-validate LLM output completeness against the deterministic extraction.
 * If the deterministic path found claims that the LLM completely missed,
 * this flags a coverage gap. The caller may choose to merge the missing
 * items rather than discard the LLM output.
 */
function verifyCoverage(llmOutput, deterministicFragments) {
    const llmText = new Set(extractAllRawFragments(llmOutput).map((f) => f.toLowerCase()));
    const missing = [];
    for (const frag of deterministicFragments) {
        const trimmed = frag.trim().toLowerCase();
        if (trimmed.length < 20)
            continue;
        let found = false;
        for (const llmFrag of llmText) {
            if (llmFrag.includes(trimmed.slice(0, 20)) || trimmed.includes(llmFrag.slice(0, 20))) {
                found = true;
                break;
            }
        }
        if (!found)
            missing.push(frag);
    }
    return { ok: missing.length === 0, missing: missing.slice(0, 5) };
}
/** Validate that output does NOT contain diagnosis/advice/empathy/causation in text fields. */
function validateMedicalBoundary(output) {
    const failures = [];
    const diagnosisPatterns = [
        /\bdiagnos(?:ed|is|e)\b/i,
        /\byou should\b/i,
        /\byou need to\b/i,
        /\bi think\b/i,
        /\bit seems like\b/i,
        /\bi understand\b/i,
        /\bi'?m here for you\b/i,
        /\byou must\b/i,
        /\btreatment (?:plan|for)\b/i,
        /\bprescribe\b/i,
        /\bcondition (?:is|was|has)\s+(?:worsening|improving|stable)\b/i,
        /\bdementia (?:test|diagnos|screen|check)\b/i,
        /\balzheimer'?s?\b/i,
        /\bearly-onset\b/i,
        /\bstage\s*\d+\b/i,
        /\bmmse\b/i,
        /\bmoca\b/i,
        /\bmild (?:cognitive|behavioral)\b/i,
        /\bmoderate (?:dementia|cognitive)\b/i,
        /\bsevere (?:dementia|cognitive)\b/i,
        /\bprobable (?:alzheimer|dementia)\b/i,
        /\bsuggest(?:ed|s)? (?:seeing|consulting|referring)\b/i,
        /\brecommend(?:ed|s)? (?:medication|therapy|treatment|seeing)\b/i,
    ];
    const textFields = [
        ...output.observations.map((o) => o.description),
        ...output.events.map((e) => e.description),
        ...output.decisions.map((d) => d.description),
        ...output.outcomes.map((o) => o.description),
        ...output.unknowns.map((u) => u.question),
        ...output.non_care_facts.map((n) => n.text),
        ...output.possible_links.map((l) => l.text),
    ];
    for (const text of textFields) {
        for (const pattern of diagnosisPatterns) {
            if (pattern.test(text)) {
                failures.push(`forbidden pattern in text: "${text.slice(0, 60)}..." matches ${pattern}`);
                break;
            }
        }
    }
    // Also check possible_link text for causation theater
    for (const link of output.possible_links) {
        if (/\bcaused\b/i.test(link.text) || /\bles? to\b/i.test(link.text)) {
            failures.push(`causation theater in possible_link: "${link.text.slice(0, 60)}..."`);
        }
    }
    return { ok: failures.length === 0, failures };
}
