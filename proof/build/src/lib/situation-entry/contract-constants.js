"use strict";
/** SolenOS event-sourced continuity — system implementation contract. */
Object.defineProperty(exports, "__esModule", { value: true });
exports.SITUATION_ENTRY_PROHIBITED = exports.TRACKING_DIMENSIONS = exports.EXTRACTED_TYPES = exports.CARE_CONTEXT_ROOT_ID = exports.SITUATION_ENTRY_IDENTITY = void 0;
exports.SITUATION_ENTRY_IDENTITY = "An event-sourced continuity system that converts messy real-world situations into structured CareEvents.";
exports.CARE_CONTEXT_ROOT_ID = "CareContextRoot";
exports.EXTRACTED_TYPES = [
    "incident",
    "observation",
    "document_fact",
    "financial_issue",
    "coordination_issue",
    "behavioral_change",
    "administrative_issue",
    "follow_up",
    "decision",
    "unprocessed_input",
    "unparsed_raw",
    "contact_event",
    "correction",
    "unknown",
];
exports.TRACKING_DIMENSIONS = [
    "mobility",
    "appetite",
    "stability",
    "daily_functioning",
    "coordination",
    "recovery",
    "financial_stability",
    "administrative_status",
];
exports.SITUATION_ENTRY_PROHIBITED = [
    "summarize instead of structure",
    "infer causes",
    "guess missing information",
    "medical diagnosis language",
    "predictive alerts",
    "pattern detection at entry",
];
