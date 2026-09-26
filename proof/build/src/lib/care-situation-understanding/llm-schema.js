"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.LlmUnderstandingOutputSchema = exports.LlmPossibleLinkSchema = exports.LlmNonCareFactSchema = exports.LlmUnknownSchema = exports.LlmOutcomeSchema = exports.LlmDecisionSchema = exports.LlmEventSchema = exports.LlmObservationSchema = void 0;
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
    return { ok: failures.length === 0, failures };
}
