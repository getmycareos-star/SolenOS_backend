"use strict";
/** Contradiction Detection Engine — preserve all truths, model transitions over time. */
Object.defineProperty(exports, "__esModule", { value: true });
exports.SAFETY_BLOCKED_CLARIFICATION = exports.MOBILITY_STATE_PATTERNS = exports.CONTRADICTION_DETECTION_RULES = exports.CHANGE_TYPES = exports.CONTRADICTION_DETECTION_DEFINING_PRINCIPLE = exports.CONTRADICTION_DETECTION_IDENTITY = void 0;
exports.CONTRADICTION_DETECTION_IDENTITY = "SolenOS never overwrites reality. It tracks how reality changes.";
exports.CONTRADICTION_DETECTION_DEFINING_PRINCIPLE = "Contradictions are expected — preserve both versions, record transition events, never silent correction.";
exports.CHANGE_TYPES = [
    "progression",
    "contradiction",
    "escalation",
    "recovery",
    "unclear_transition",
];
exports.CONTRADICTION_DETECTION_RULES = [
    "never_overwrite_history",
    "preserve_all_events",
    "transition_events_first_class",
    "clarify_only_when_safety_blocked",
    "care_context_derived_only",
];
exports.MOBILITY_STATE_PATTERNS = [
    { pattern: /\b(walks?\s+independently|independent(?:ly)?|ambulat\w*)\b/i, state: "independent walking" },
    { pattern: /\b(fell|fall|fallen|tripped)\b/i, state: "fall event" },
    { pattern: /\b(walker|walking stick|cane)\b/i, state: "walker required" },
    { pattern: /\b(wheelchair|wheel chair)\b/i, state: "wheelchair required" },
];
exports.SAFETY_BLOCKED_CLARIFICATION = [
    "When did this change begin?",
    "Was decline sudden or gradual?",
    "Was equipment prescribed or self-adopted?",
    "Are there missing events between these observations?",
];
