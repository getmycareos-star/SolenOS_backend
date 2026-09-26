"use strict";
/**
 * Post-Care Insight Signal — observational label only (NOT a system mode).
 */
Object.defineProperty(exports, "__esModule", { value: true });
exports.POST_CARE_LOW_CONFIDENCE_THRESHOLD = exports.POST_CARE_OBSERVATION_TAG_PREFIX = exports.POST_CARE_INSIGHT_FORBIDDEN_USES = exports.POST_CARE_INSIGHT_ANTI_DRIFT_RULES = exports.CARE_CONTEXT_STATES = exports.POST_CARE_INSIGHT_ONE_LINE_TRUTH = exports.POST_CARE_INSIGHT_BOUNDARY = void 0;
exports.POST_CARE_INSIGHT_BOUNDARY = "care_context_state is LABEL ONLY — a shallow surface-signal for telemetry observation; it does NOT create a system mode, route lifecycle, or branch UX.";
exports.POST_CARE_INSIGHT_ONE_LINE_TRUTH = "Observing care context does not change what SolenOS is — it records a surface signal for measurement only.";
exports.CARE_CONTEXT_STATES = [
    "active_care",
    "crisis",
    "post_care",
    "uncertain",
];
exports.POST_CARE_INSIGHT_ANTI_DRIFT_RULES = [
    "Observing a state does NOT create a system mode",
    "care_context_state is LABEL ONLY — no behavioral branching beyond optional verbosity tweak",
    "No UX changes, no lifecycle routing, no output schema changes",
    "Telemetry persistence is observational — not profiling or segmentation",
];
exports.POST_CARE_INSIGHT_FORBIDDEN_USES = [
    "lifecycle routing",
    "UI branching",
    "output schema changes",
    "state machine transitions",
    "user profiling",
    "care journey tracking",
    "segmentation",
    "emotional state engine",
    "post-care product mode",
];
exports.POST_CARE_OBSERVATION_TAG_PREFIX = "CARE_CONTEXT_STATE:";
exports.POST_CARE_LOW_CONFIDENCE_THRESHOLD = 0.55;
