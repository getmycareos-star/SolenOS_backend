"use strict";
/** Deterministic ranking over CareEvents — NOT AI reasoning. */
Object.defineProperty(exports, "__esModule", { value: true });
exports.PRIORITY_WEIGHTS = exports.ATTENTION_PANEL_THRESHOLD = exports.UI_SURFACE_LIMIT = exports.CONTEXTUAL_THRESHOLD = exports.IMPORTANT_THRESHOLD = exports.CRITICAL_THRESHOLD = exports.PROVISIONAL_UNCERTAINTY = exports.DEFAULT_DEPENDENCY_COUNT = exports.DEFAULT_URGENCY = exports.DEFAULT_UNCERTAINTY = exports.ATTENTION_STATUSES = exports.PRIORITY_TIERS = exports.CARE_EVENT_PRIORITY_IDENTITY = void 0;
exports.CARE_EVENT_PRIORITY_IDENTITY = "A prioritization layer over an evolving event graph that decides what deserves human attention at any moment.";
exports.PRIORITY_TIERS = ["CRITICAL", "IMPORTANT", "CONTEXTUAL", "BACKGROUND"];
exports.ATTENTION_STATUSES = ["active", "provisional", "resolved", "invalidated"];
exports.DEFAULT_UNCERTAINTY = 70;
exports.DEFAULT_URGENCY = 30;
exports.DEFAULT_DEPENDENCY_COUNT = 1;
exports.PROVISIONAL_UNCERTAINTY = 80;
exports.CRITICAL_THRESHOLD = 80;
exports.IMPORTANT_THRESHOLD = 50;
exports.CONTEXTUAL_THRESHOLD = 20;
exports.UI_SURFACE_LIMIT = 5;
exports.ATTENTION_PANEL_THRESHOLD = 80;
/** Weights — ONLY allowed computation (spec §2). */
exports.PRIORITY_WEIGHTS = {
    urgency: 0.35,
    uncertainty: 0.25,
    dependency: 0.25,
    recency: 0.15,
};
