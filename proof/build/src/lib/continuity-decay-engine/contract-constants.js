"use strict";
/** Continuity Decay Engine — confidence in CareContext freshness, not patient health. */
Object.defineProperty(exports, "__esModule", { value: true });
exports.REFRESH_QUESTION_TEMPLATES = exports.DECAY_PIPELINE_STAGES = exports.CONFIDENCE_GAP_THRESHOLD = exports.FRESHNESS_WINDOW_DAYS = exports.FRESHNESS_TIERS = exports.DECAY_PROHIBITED = exports.DECAY_ENGINE_BOUNDARY = exports.CONTINUITY_DECAY_IDENTITY = void 0;
exports.CONTINUITY_DECAY_IDENTITY = "SolenOS distinguishes reality, known reality, and confidence — never mistaking old information for current understanding.";
exports.DECAY_ENGINE_BOUNDARY = "Model how trustworthy the CareContext remains over time — never infer health status or caregiver performance.";
exports.DECAY_PROHIBITED = [
    "assume no update means no change",
    "fixed inactivity thresholds ignoring family rhythm",
    "generic nag reminders without context",
    "reset entire CareContext confidence on single confirmation",
    "conflate confidence with medical severity",
    "diagnose from stale information gaps",
    "hidden refresh reasoning",
];
exports.FRESHNESS_TIERS = ["long_lived", "medium_lived", "short_lived"];
/** Days until confidence meaningfully decays — per information type. */
exports.FRESHNESS_WINDOW_DAYS = {
    long_lived: 365,
    medium_lived: 60,
    short_lived: 7,
};
exports.CONFIDENCE_GAP_THRESHOLD = 60;
exports.DECAY_PIPELINE_STAGES = [
    "freshness_assessment",
    "object_confidence",
    "family_rhythm",
    "expected_follow_ups",
    "continuity_gaps",
    "refresh_planner",
    "priority_influence",
    "confidence_recovery",
];
exports.REFRESH_QUESTION_TEMPLATES = [
    "Has medication changed since your last update?",
    "Any falls or safety incidents?",
    "Any hospital visits or discharge changes?",
    "Any new symptoms or concerns?",
    "Has sleep or daily routine changed?",
    "Anything worrying you today?",
];
