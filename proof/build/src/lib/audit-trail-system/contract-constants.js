"use strict";
/** Audit Trail System — immutable versioned reality recorder. */
Object.defineProperty(exports, "__esModule", { value: true });
exports.AUDIT_IMMUTABILITY_RULES = exports.CONFLICT_RELATIONSHIPS = exports.AUDIT_REASONS = exports.AUDIT_ACTION_TYPES = exports.AUDIT_ACTORS = exports.AUDIT_TRAIL_DEFINING_PRINCIPLE = exports.AUDIT_TRAIL_IDENTITY = void 0;
exports.AUDIT_TRAIL_IDENTITY = "SolenOS does not just store the current state of care. It stores the entire history of how that state evolved.";
exports.AUDIT_TRAIL_DEFINING_PRINCIPLE = "No state change is valid unless it is explainable, attributable, and historically recoverable.";
exports.AUDIT_ACTORS = ["caregiver", "system", "ai_engine", "clinician"];
exports.AUDIT_ACTION_TYPES = [
    "create",
    "update",
    "delete",
    "merge",
    "infer",
    "correct",
];
exports.AUDIT_REASONS = [
    "explicit_user_input",
    "system_inference",
    "clarification_response",
    "pattern_update",
    "contradiction_resolution",
    "external_clinical_record",
    "carecontext_recompute",
];
exports.CONFLICT_RELATIONSHIPS = ["correction", "contradiction", "refinement"];
exports.AUDIT_IMMUTABILITY_RULES = [
    "append_only",
    "never_delete",
    "never_overwrite",
    "corrections_are_new_entries",
    "audit_integrity_wins_over_performance",
];
