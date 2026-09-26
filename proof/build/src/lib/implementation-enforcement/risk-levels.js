"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.RISK_RANK = exports.RISK_LEVEL_DEFINITIONS = exports.SOLENOS_RISK_LEVELS = void 0;
exports.normalizeRiskLevel = normalizeRiskLevel;
/** Canonical risk levels — lowercase per Final System Spec (uppercase accepted at boundary). */
exports.SOLENOS_RISK_LEVELS = ["low", "medium", "high", "critical"];
exports.RISK_LEVEL_DEFINITIONS = {
    low: "minimal urgency",
    medium: "requires attention",
    high: "urgent concern",
    critical: "possible emergency requiring immediate escalation",
};
const LEGACY_UPPERCASE_MAP = {
    LOW: "low",
    MEDIUM: "medium",
    HIGH: "high",
    CRITICAL: "critical",
};
/** Normalize risk values at validation boundary — accepts legacy uppercase and canonical lowercase. */
function normalizeRiskLevel(value) {
    if (typeof value !== "string")
        return null;
    if (exports.SOLENOS_RISK_LEVELS.includes(value)) {
        return value;
    }
    const upper = value.toUpperCase();
    if (upper in LEGACY_UPPERCASE_MAP) {
        return LEGACY_UPPERCASE_MAP[upper];
    }
    return LEGACY_UPPERCASE_MAP[value] ?? null;
}
exports.RISK_RANK = {
    low: 0,
    medium: 1,
    high: 2,
    critical: 3,
};
