"use strict";
/** Care Transparency Layer — no output is valid unless reasoning is visible. */
Object.defineProperty(exports, "__esModule", { value: true });
exports.TRANSPARENCY_RULES = exports.DECAY_STATUSES = exports.CONFIDENCE_TIERS = exports.EVIDENCE_TYPES = exports.CARE_TRANSPARENCY_DEFINING_PRINCIPLE = exports.CARE_TRANSPARENCY_IDENTITY = void 0;
exports.CARE_TRANSPARENCY_IDENTITY = "Solenos does not ask for trust. It earns it through visibility of reasoning.";
exports.CARE_TRANSPARENCY_DEFINING_PRINCIPLE = "Every output is invalid if it does not include a complete Care Transparency Panel.";
exports.EVIDENCE_TYPES = [
    "observation",
    "inference",
    "external_report",
    "system_pattern",
];
exports.CONFIDENCE_TIERS = ["high", "medium", "low"];
exports.DECAY_STATUSES = ["fresh", "aging", "stale"];
exports.TRANSPARENCY_RULES = [
    "data_used_explicit",
    "data_ignored_explicit",
    "observation_inference_split",
    "confidence_on_key_statements",
    "recency_visible",
    "no_hidden_inference",
];
