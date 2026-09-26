"use strict";
/** Resolution Engine — lifecycle ownership for operational situations. */
Object.defineProperty(exports, "__esModule", { value: true });
exports.REEVALUATION_MAX_AGE_MS = exports.DEFAULT_RETENTION_DAYS = exports.FORBIDDEN_RESOLUTION_TRIGGERS = exports.RESOLUTION_EVIDENCE_KINDS = exports.SITUATION_LIFECYCLE_STATUSES = exports.RESOLUTION_ENGINE_LAYER_FORBIDDEN = exports.RESOLUTION_ENGINE_LAYER_PIPELINE_POSITION = exports.RESOLUTION_ENGINE_LAYER_ONE_LINE_TRUTH = exports.RESOLUTION_ENGINE_LAYER_IDENTITY = void 0;
exports.RESOLUTION_ENGINE_LAYER_IDENTITY = "a situation lifecycle layer that determines when a situation is no longer operationally active — completeness requires evidence of outcome achieved, abandoned, or superseded, never time or inactivity";
exports.RESOLUTION_ENGINE_LAYER_ONE_LINE_TRUTH = "A situation is complete because its outcome has been achieved, abandoned, or superseded — never because time passed or the user went quiet.";
exports.RESOLUTION_ENGINE_LAYER_PIPELINE_POSITION = "RESOLUTION ENGINE LAYER — after Care Context / identity continuity signals; filters ACTIVE situations for Priority Engine and Risk consumers; before Action Generator";
exports.RESOLUTION_ENGINE_LAYER_FORBIDDEN = [
    "auto-resolve because elapsed time passed",
    "auto-resolve because of inactivity",
    "auto-resolve because of lack of user interaction",
    "auto-resolve because of low confidence",
    "auto-resolve from system assumptions without evidence",
    "delete timeline, memory, or documents on resolve or archive",
    "resurrect ARCHIVED situations — create a new ACTIVE situation instead",
    "reverse transitions RESOLVED→ACTIVE or ARCHIVED→RESOLVED automatically",
];
exports.SITUATION_LIFECYCLE_STATUSES = ["ACTIVE", "RESOLVED", "ARCHIVED"];
/** Valid evidence kinds for ACTIVE → RESOLVED. */
exports.RESOLUTION_EVIDENCE_KINDS = [
    "COMPLETION_EVENT",
    "APPROVAL_EVENT",
    "FULFILLMENT_EVENT",
    "USER_CONFIRMATION",
    "SUPERSEDING_EVENT",
];
/** Triggers that MUST NEVER cause auto-resolution. */
exports.FORBIDDEN_RESOLUTION_TRIGGERS = [
    "ELAPSED_TIME",
    "INACTIVITY",
    "LACK_OF_USER_INTERACTION",
    "LOW_CONFIDENCE",
    "SYSTEM_ASSUMPTION",
];
/** Default retention window before RESOLVED may archive (days) — archival optimization only. */
exports.DEFAULT_RETENTION_DAYS = 30;
/** ACTIVE situations must be reevaluated at least this often (ms) for system guarantee. */
exports.REEVALUATION_MAX_AGE_MS = 7 * 24 * 60 * 60 * 1000;
