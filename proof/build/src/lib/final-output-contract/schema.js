"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.FinalOutputValidationError = exports.FinalOutputContractSchema = exports.TransparencyPanelSchema = exports.TrustLayerSchema = exports.ConfidenceStateSchema = exports.DecisionTraceSchema = void 0;
exports.validateFinalOutput = validateFinalOutput;
exports.isFinalOutputValidationError = isFinalOutputValidationError;
exports.extractFinalOutputPayload = extractFinalOutputPayload;
exports.ensureFinalOutputShape = ensureFinalOutputShape;
const zod_1 = require("zod");
const contract_constants_1 = require("./contract-constants");
const degrade_1 = require("./degrade");
function normalizeCanonicalRisk(value) {
    if (value === "critical" || value === "high")
        return "high";
    if (value === "medium")
        return "medium";
    if (value === "low")
        return "low";
    return "medium";
}
function ensureFinalOutputShape(input) {
    if (!input || typeof input !== "object")
        return input;
    const obj = input;
    return {
        what_is_happening: obj.what_is_happening ?? "",
        what_matters_now: obj.what_matters_now ?? "",
        what_to_ask_next: obj.what_to_ask_next ?? "",
        risk_level: normalizeCanonicalRisk(obj.risk_level),
        what_can_wait: obj.what_can_wait ?? "",
        follow_up_items: Array.isArray(obj.follow_up_items) ? obj.follow_up_items : [],
        decision_trace: obj.decision_trace && typeof obj.decision_trace === "object"
            ? obj.decision_trace
            : (0, degrade_1.createEmptyDecisionTrace)(),
        confidence_state: obj.confidence_state && typeof obj.confidence_state === "object"
            ? obj.confidence_state
            : (0, degrade_1.createEmptyConfidenceState)(),
        trust_layer: obj.trust_layer && typeof obj.trust_layer === "object"
            ? obj.trust_layer
            : (0, degrade_1.createEmptyTrustLayer)(),
        transparency_panel: obj.transparency_panel && typeof obj.transparency_panel === "object"
            ? obj.transparency_panel
            : (0, degrade_1.createEmptyTransparencyPanel)(),
    };
}
exports.DecisionTraceSchema = zod_1.z
    .object({
    events: zod_1.z.array(zod_1.z.string()),
    assumptions: zod_1.z.array(zod_1.z.string()),
    unknowns: zod_1.z.array(zod_1.z.string()),
    evidence_sources: zod_1.z.array(zod_1.z.string()),
})
    .strict();
exports.ConfidenceStateSchema = zod_1.z
    .object({
    overall_confidence: zod_1.z.enum(contract_constants_1.CANONICAL_CONFIDENCE_LEVELS),
    completeness: zod_1.z.number().min(0).max(100),
    reasoning_limits: zod_1.z.array(zod_1.z.string()),
})
    .strict();
exports.TrustLayerSchema = zod_1.z
    .object({
    known: zod_1.z.array(zod_1.z.object({
        statement: zod_1.z.string(),
        source: zod_1.z.string(),
        source_type: zod_1.z.enum(["care_event", "caregiver_input", "document", "care_context"]),
        source_event_id: zod_1.z.string().optional(),
    })),
    assumed: zod_1.z.array(zod_1.z.object({
        statement: zod_1.z.string(),
        reasoning_basis: zod_1.z.string(),
        source_engine: zod_1.z.string(),
    })),
    unknown: zod_1.z.array(zod_1.z.object({
        statement: zod_1.z.string(),
        drives_clarification: zod_1.z.boolean(),
    })),
    recency: zod_1.z.object({
        last_updated_at: zod_1.z.string().nullable(),
        freshness_score: zod_1.z.number().min(0).max(1),
        interpretation: zod_1.z.string(),
    }),
    confidence: zod_1.z.number().min(0).max(1),
})
    .strict();
exports.TransparencyPanelSchema = zod_1.z
    .object({
    data_used: zod_1.z.object({
        care_events: zod_1.z.array(zod_1.z.string()),
        timeline_segments: zod_1.z.array(zod_1.z.string()),
        caregiver_inputs: zod_1.z.array(zod_1.z.string()),
    }),
    data_ignored: zod_1.z.object({
        conflicting: zod_1.z.array(zod_1.z.string()),
        low_confidence: zod_1.z.array(zod_1.z.string()),
        stale_or_decayed: zod_1.z.array(zod_1.z.string()),
    }),
    reason_for_output: zod_1.z.string().min(1),
    evidence_breakdown: zod_1.z.array(zod_1.z.object({
        conclusion: zod_1.z.string(),
        evidence_type: zod_1.z.enum(["observation", "inference", "external_report", "system_pattern"]),
        confidence_pct: zod_1.z.number().min(0).max(100),
    })),
    confidence_scores: zod_1.z.object({
        overall_pct: zod_1.z.number().min(0).max(100),
        tier: zod_1.z.enum(["high", "medium", "low"]),
    }),
    recency: zod_1.z.object({
        last_update_at: zod_1.z.string().nullable(),
        critical_event_ages: zod_1.z.array(zod_1.z.string()),
        decay_status: zod_1.z.enum(["fresh", "aging", "stale"]),
    }),
    observed: zod_1.z.array(zod_1.z.string()),
    inferred: zod_1.z.array(zod_1.z.string()),
})
    .strict();
exports.FinalOutputContractSchema = zod_1.z.preprocess(ensureFinalOutputShape, zod_1.z
    .object({
    what_is_happening: zod_1.z.string().min(1),
    what_matters_now: zod_1.z.string().min(1),
    what_to_ask_next: zod_1.z.string().min(1),
    risk_level: zod_1.z.enum(contract_constants_1.CANONICAL_RISK_LEVELS),
    what_can_wait: zod_1.z.string().min(1),
    follow_up_items: zod_1.z.array(zod_1.z.string()),
    decision_trace: exports.DecisionTraceSchema,
    confidence_state: exports.ConfidenceStateSchema,
    trust_layer: exports.TrustLayerSchema,
    transparency_panel: exports.TransparencyPanelSchema,
})
    .strict());
class FinalOutputValidationError extends Error {
    type = "INVALID_FINAL_OUTPUT";
    raw_output;
    constructor(message, raw_output) {
        super(message);
        this.name = "FinalOutputValidationError";
        this.raw_output = raw_output;
    }
}
exports.FinalOutputValidationError = FinalOutputValidationError;
function validateFinalOutput(output) {
    const result = exports.FinalOutputContractSchema.safeParse(output);
    if (result.success) {
        return result.data;
    }
    throw new FinalOutputValidationError("Output failed strict final output contract validation", output);
}
function isFinalOutputValidationError(error) {
    return (typeof error === "object" &&
        error !== null &&
        error.type === "INVALID_FINAL_OUTPUT");
}
function extractFinalOutputPayload(output) {
    if (!output || typeof output !== "object")
        return output;
    const obj = output;
    if (obj.final_output && typeof obj.final_output === "object") {
        return obj.final_output;
    }
    return {
        what_is_happening: obj.what_is_happening,
        what_matters_now: obj.what_matters_now,
        what_to_ask_next: obj.what_to_ask_next,
        risk_level: obj.risk_level,
        what_can_wait: obj.what_can_wait,
        follow_up_items: obj.follow_up_items,
        decision_trace: obj.decision_trace,
        confidence_state: obj.confidence_state,
        trust_layer: obj.trust_layer,
        transparency_panel: obj.transparency_panel,
    };
}
