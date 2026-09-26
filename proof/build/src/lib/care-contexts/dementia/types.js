"use strict";
/** Dementia context V1 — storage and display only; no inference or prediction. */
Object.defineProperty(exports, "__esModule", { value: true });
exports.DRIVING_STATUSES = exports.MEDICATION_RISK_LEVELS = exports.DEMENTIA_STAGES = void 0;
exports.DEMENTIA_STAGES = ["early", "moderate", "late", "unspecified"];
exports.MEDICATION_RISK_LEVELS = [
    "independent",
    "needs_supervision",
    "needs_full_administration",
];
exports.DRIVING_STATUSES = [
    "still_driving",
    "recently_stopped",
    "conversation_pending",
    "not_applicable",
];
