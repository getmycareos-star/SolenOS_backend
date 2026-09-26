"use strict";
/** Care Timeline Engine — chronological, deduplicated medical truth over time. */
Object.defineProperty(exports, "__esModule", { value: true });
exports.TASK_INTENT_PATTERN = exports.MED_CHANGE_PATTERN = exports.MED_START_PATTERN = exports.DOSAGE_PATTERN = exports.MEDICATION_PATTERN = exports.RECENT_EVENT_DAYS = exports.DEDUP_WINDOW_MS = exports.CARE_TIMELINE_RULES = exports.SOURCE_CHANNELS = exports.FACT_STATUSES = exports.MEDICAL_FACT_TYPES = exports.TIMELINE_EVENT_TYPES = exports.CARE_TIMELINE_DEFINING_PRINCIPLE = exports.CARE_TIMELINE_ENGINE_IDENTITY = void 0;
exports.CARE_TIMELINE_ENGINE_IDENTITY = "Build a state-driven Care Timeline Engine that converts unstructured caregiver inputs into deduplicated medical facts, chronological events, and a continuously evolving patient state.";
exports.CARE_TIMELINE_DEFINING_PRINCIPLE = "The Care Record is a continuously mutating truth object — events are state drivers, not logs.";
exports.TIMELINE_EVENT_TYPES = [
    "medication_started",
    "medication_changed",
    "symptom_reported",
    "doctor_instruction",
    "appointment",
    "test_result",
    "care_note",
];
exports.MEDICAL_FACT_TYPES = ["medication", "condition", "symptom"];
exports.FACT_STATUSES = ["active", "resolved", "unknown"];
exports.SOURCE_CHANNELS = ["whatsapp", "voice", "pdf", "manual"];
exports.CARE_TIMELINE_RULES = [
    "chronological_ordering",
    "semantic_deduplication",
    "every_input_mutates_record",
    "contradictions_become_anomalies",
    "evidence_trail_required",
    "pure_state_reducer",
];
exports.DEDUP_WINDOW_MS = 72 * 60 * 60 * 1000;
exports.RECENT_EVENT_DAYS = 7;
exports.MEDICATION_PATTERN = /\b(metformin|insulin|aspirin|lisinopril|atorvastatin|warfarin|medication|medicine|prescription)\b/i;
exports.DOSAGE_PATTERN = /\b(\d+(?:\.\d+)?\s*(?:mg|mcg|g|units?|ml))\b/i;
exports.MED_START_PATTERN = /\b(started|prescribed|began|initiated|new)\b/i;
exports.MED_CHANGE_PATTERN = /\b(changed|increased|decreased|reduced|adjusted|dose)\b/i;
exports.TASK_INTENT_PATTERN = /\b(book|schedule|refill|follow[- ]?up|take (?:him|her|them) to|call|confirm|monitor)\b/i;
