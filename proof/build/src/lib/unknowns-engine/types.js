"use strict";
/**
 * Explicit Unknowns — disease-agnostic schema.
 * Dementia is the first clinical profile, not the engine architecture.
 */
Object.defineProperty(exports, "__esModule", { value: true });
exports.UNKNOWN_DERIVATIONS = exports.UNKNOWN_STATUSES = exports.UNKNOWN_PRIORITIES = void 0;
exports.UNKNOWN_PRIORITIES = ["critical", "high", "medium", "low"];
exports.UNKNOWN_STATUSES = ["unresolved", "partially_resolved", "resolved"];
exports.UNKNOWN_DERIVATIONS = [
    "missing_field",
    "incomplete_timeline",
    "conflict",
    "clinical_gap",
    "pattern_requirement",
];
