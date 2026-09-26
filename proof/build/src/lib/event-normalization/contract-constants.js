"use strict";
/** Event Normalization — atomicity, dedup, split, confidence tiers. */
Object.defineProperty(exports, "__esModule", { value: true });
exports.SPLIT_VERB_PATTERNS = exports.NOISE_PATTERNS = exports.ATOMIC_EVENT_TYPES = exports.DEDUP_TIME_WINDOW_MS = exports.CONFIDENCE_NEEDS_REVIEW = exports.CONFIDENCE_AUTO_COMMIT = exports.NORMALIZER_IDENTITY = void 0;
exports.NORMALIZER_IDENTITY = "Every CareEvent must represent a single, durable, non-ambiguous unit of reality change.";
exports.CONFIDENCE_AUTO_COMMIT = 0.85;
exports.CONFIDENCE_NEEDS_REVIEW = 0.65;
exports.DEDUP_TIME_WINDOW_MS = 48 * 60 * 60 * 1000;
exports.ATOMIC_EVENT_TYPES = [
    "medication_started",
    "medication_changed",
    "appointment_occurred",
    "symptom_observed",
    "document_received",
    "financial_claim_rejected",
    "care_instruction_given",
    "incident_occurred",
    "communication_occurred",
    "unprocessed_input",
    "correction",
];
exports.NOISE_PATTERNS = [
    { pattern: /\b(feels?\s+tired|a bit tired)\b/i, attach_to: "symptom_observed" },
    { pattern: /\b(a bit better|slightly better|improving)\b/i, attach_to: "symptom_observed" },
    { pattern: /\b(doctor called|nurse called)\b/i, attach_to: "communication_occurred" },
    { pattern: /\b(insurance form received|form received)\b/i, merge_into: "document_received" },
];
exports.SPLIT_VERB_PATTERNS = [
    { pattern: /\b(fell|fall|fallen|tripped|slipped)\b/i, type: "incident_occurred" },
    { pattern: /\b(hospital|er\b|emergency|admitted)\b/i, type: "appointment_occurred" },
    { pattern: /\b(medication|prescribed|dose|medication changed|started)\b/i, type: "medication_changed" },
    { pattern: /\b(appointment|visit|saw doctor)\b/i, type: "appointment_occurred" },
    { pattern: /\b(claim rejected|insurance rejected|denied)\b/i, type: "financial_claim_rejected" },
    { pattern: /\b(received|document|letter|summary)\b/i, type: "document_received" },
    { pattern: /\b(instruction|must|should|monitor|watch for)\b/i, type: "care_instruction_given" },
    { pattern: /\b(confus\w*|symptom|pain|appetite|eating)\b/i, type: "symptom_observed" },
];
