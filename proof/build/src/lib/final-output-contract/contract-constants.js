"use strict";
/** Final output contract — the one and only allowed output structure. */
Object.defineProperty(exports, "__esModule", { value: true });
exports.LLM_OUTPUT_SCHEMA_JSON = exports.MAX_FOLLOW_UP_ITEMS = exports.MAX_HIGH_IMPACT_QUESTIONS = exports.CONFIDENCE_STATE_FIELDS = exports.DECISION_TRACE_FIELDS = exports.REQUIRED_OUTPUT_FIELDS = exports.CANONICAL_CONFIDENCE_LEVELS = exports.CANONICAL_RISK_LEVELS = exports.FINAL_OUTPUT_CONTRACT_IDENTITY = void 0;
exports.FINAL_OUTPUT_CONTRACT_IDENTITY = "SolenOS has exactly one canonical output schema — no alternative response formats.";
exports.CANONICAL_RISK_LEVELS = ["low", "medium", "high"];
exports.CANONICAL_CONFIDENCE_LEVELS = ["low", "medium", "high"];
exports.REQUIRED_OUTPUT_FIELDS = [
    "what_is_happening",
    "what_matters_now",
    "what_to_ask_next",
    "risk_level",
    "what_can_wait",
    "follow_up_items",
    "decision_trace",
    "confidence_state",
    "trust_layer",
];
exports.DECISION_TRACE_FIELDS = [
    "events",
    "assumptions",
    "unknowns",
    "evidence_sources",
];
exports.CONFIDENCE_STATE_FIELDS = [
    "overall_confidence",
    "completeness",
    "reasoning_limits",
];
exports.MAX_HIGH_IMPACT_QUESTIONS = 3;
exports.MAX_FOLLOW_UP_ITEMS = 8;
/** LLM envelope — the only allowed JSON output target. */
exports.LLM_OUTPUT_SCHEMA_JSON = '{ what_is_happening: string, what_matters_now: string, what_to_ask_next: string, risk_level: "low" | "medium" | "high", what_can_wait: string, follow_up_items: string[], decision_trace: { events: string[], assumptions: string[], unknowns: string[], evidence_sources: string[] }, confidence_state: { overall_confidence: "low" | "medium" | "high", completeness: number, reasoning_limits: string[] }, trust_layer: { known: object[], assumed: object[], unknown: object[], recency: { last_updated_at: string | null, freshness_score: number, interpretation: string }, confidence: number } }';
