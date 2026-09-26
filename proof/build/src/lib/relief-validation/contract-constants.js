"use strict";
/** Relief Validation Layer — output effectiveness validation only. */
Object.defineProperty(exports, "__esModule", { value: true });
exports.RELIEF_CLASSIFICATION_RULES = exports.RELIEF_VALIDATION_FINAL_TRUTH = exports.RELIEF_VALIDATION_DRIFT_PREVENTION = exports.RELIEF_VALIDATION_RECORD_FIELDS = exports.RELIEF_OUTCOMES = exports.RELIEF_VALIDATION_FORBIDDEN_FLOW = exports.RELIEF_VALIDATION_ARCHITECTURE_FLOW = exports.RELIEF_VALIDATION_FORBIDDEN_IDENTITY = exports.RELIEF_VALIDATION_FORBIDDEN_MEASURES = exports.RELIEF_VALIDATION_MEASURES = exports.RELIEF_VALIDATION_PURPOSE = exports.RELIEF_VALIDATION_ONE_LINE_TRUTH = exports.RELIEF_VALIDATION_IDENTITY = void 0;
exports.RELIEF_VALIDATION_IDENTITY = "output effectiveness validation";
exports.RELIEF_VALIDATION_ONE_LINE_TRUTH = "SolenOS does not measure people. SolenOS measures whether its structured cognitive decomposition reliably reduces uncertainty and cognitive burden on an interaction-by-interaction basis.";
exports.RELIEF_VALIDATION_PURPOSE = "Did SolenOS reduce cognitive burden for this interaction? Nothing else.";
exports.RELIEF_VALIDATION_MEASURES = [
    "cognitive decompression success",
    "clarity achievement",
    "prioritization effectiveness",
    "uncertainty reduction",
];
exports.RELIEF_VALIDATION_FORBIDDEN_MEASURES = [
    "engagement",
    "retention",
    "satisfaction",
    "behavior",
    "productivity",
    "caregiving performance",
    "user value",
];
exports.RELIEF_VALIDATION_FORBIDDEN_IDENTITY = [
    "analytics",
    "CRM",
    "behavioral tracking",
    "personalization",
    "user intelligence",
];
exports.RELIEF_VALIDATION_ARCHITECTURE_FLOW = "INPUT → Cognitive Decomposition → Structured Output → Relief Validation";
exports.RELIEF_VALIDATION_FORBIDDEN_FLOW = "INPUT → User Modeling → Personalization → Adaptive Behavior";
exports.RELIEF_OUTCOMES = ["high", "partial", "none", "failure"];
exports.RELIEF_VALIDATION_RECORD_FIELDS = [
    "interaction_id",
    "input_category",
    "output_structured",
    "structure_valid",
    "semantic_valid",
    "latency_ms",
    "risk_level",
    "relief_outcome",
    "requery_detected",
    "helpful_feedback",
];
exports.RELIEF_VALIDATION_DRIFT_PREVENTION = "Relief validation MUST NEVER evolve into engagement optimization, retention optimization, user scoring, personalization, or behavioral prediction.";
exports.RELIEF_VALIDATION_FINAL_TRUTH = "Persistence exists to validate whether structured cognitive decomposition consistently reduces uncertainty. Persistence does NOT exist to understand, profile, predict, or optimize users.";
exports.RELIEF_CLASSIFICATION_RULES = {
    high: "no re-query, no clarification request, positive feedback OR clean completion",
    partial: "understanding appears improved, some clarification still required",
    none: "confusion remains or outcome unknown — success never assumed",
    failure: "repeated confusion, re-query loops, or explicit negative feedback",
};
