"use strict";
/** Unified CareEvent — input-method agnostic caregiver knowledge record. */
Object.defineProperty(exports, "__esModule", { value: true });
exports.UNCERTAINTY_LEVELS = exports.CARE_EVENT_TYPES = exports.CARE_EVENT_SOURCE_TYPES = void 0;
exports.CARE_EVENT_SOURCE_TYPES = [
    "voice",
    "text",
    "document",
    "photo",
    "message",
];
exports.CARE_EVENT_TYPES = [
    "observation",
    "fall",
    "medication_change",
    "symptom",
    "appointment",
    "behavior",
    "task",
    "unknown",
];
exports.UNCERTAINTY_LEVELS = ["low", "medium", "high"];
