"use strict";
/** Network effect & data moat — compounding continuity, not AI. */
Object.defineProperty(exports, "__esModule", { value: true });
exports.EVENT_MATCH_WINDOW_DAYS = exports.ENTITY_MATCH_THRESHOLD = exports.MATURITY_MESSAGES = exports.MATURITY_STAGES = exports.ENRICHMENT_ACTION_TYPES = exports.INTERACTION_OUTCOME_TYPES = exports.NON_COMPOUNDING_TYPES = exports.COMPOUNDING_ASSET_TYPES = exports.NETWORK_EFFECT_MOAT_IDENTITY = void 0;
exports.NETWORK_EFFECT_MOAT_IDENTITY = "The moat is continuously compounding continuity data — not AI.";
exports.COMPOUNDING_ASSET_TYPES = [
    "care_history",
    "relationships",
    "continuity",
    "user_corrections",
    "resolved_uncertainty",
];
exports.NON_COMPOUNDING_TYPES = [
    "llm_response",
    "chat_conversation",
    "summary",
    "prompt",
    "uploaded_file_unstructured",
    "ai_generated_text",
];
exports.INTERACTION_OUTCOME_TYPES = [
    "new_care_event",
    "refined_care_event",
    "resolved_uncertainty",
    "new_relationship",
    "corrected_fact",
    "completed_follow_up",
    "new_entity",
    "updated_timeline",
];
exports.ENRICHMENT_ACTION_TYPES = [
    "link_to_existing_event",
    "enrich_entity",
    "resolve_uncertainty",
    "strengthen_relationship",
    "update_timeline",
    "close_follow_up",
];
exports.MATURITY_STAGES = [
    "early",
    "building",
    "established",
    "journey",
];
exports.MATURITY_MESSAGES = {
    early: "SolenOS understands today's situation.",
    building: "SolenOS remembers everything that has happened.",
    established: "SolenOS understands how everything connects.",
    journey: "SolenOS preserves the continuity of an entire care journey.",
};
exports.ENTITY_MATCH_THRESHOLD = 0.6;
exports.EVENT_MATCH_WINDOW_DAYS = 90;
