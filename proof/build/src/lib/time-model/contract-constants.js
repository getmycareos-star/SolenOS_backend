"use strict";
/** SolenOS dual time model — event_time vs ingestion_time. */
Object.defineProperty(exports, "__esModule", { value: true });
exports.PATTERN_WINDOW_ANCHOR = exports.RETIMING_REASONS = exports.EVENT_TIME_TYPES = exports.TIME_MODEL_IDENTITY = void 0;
exports.TIME_MODEL_IDENTITY = "Time is a conflicting interpretation system that preserves both truth and uncertainty.";
exports.EVENT_TIME_TYPES = ["exact", "approximate", "range", "unknown"];
exports.RETIMING_REASONS = [
    "user_correction",
    "retrospective_update",
    "late_arrival",
];
exports.PATTERN_WINDOW_ANCHOR = "event_time";
