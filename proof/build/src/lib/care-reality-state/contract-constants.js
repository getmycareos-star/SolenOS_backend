"use strict";
/**
 * Care Reality State — continuously updated understanding of the person's care reality.
 * Not a note. Not an event. Not a timeline. Single source of truth for caregiver responses.
 */
Object.defineProperty(exports, "__esModule", { value: true });
exports.COGNITIVE_LOAD_PRIMARY_QUESTIONS = exports.DISCLOSURE_SECTIONS_BY_STAGE = exports.CARE_REALITY_STATE_NEVER = exports.CARE_REALITY_FORBIDDEN_INTERNAL_QUESTION = exports.CARE_REALITY_INTERNAL_QUESTION = exports.CARE_REALITY_DISCLOSURE_STAGES = exports.CARE_REALITY_STATE_CHAIN = exports.CARE_REALITY_STATE_PURPOSE = exports.CARE_REALITY_STATE_IDENTITY = void 0;
exports.CARE_REALITY_STATE_IDENTITY = "Care Reality State — what solenos currently understands about this person's care reality from available evidence.";
exports.CARE_REALITY_STATE_PURPOSE = "Every caregiver response is generated from Care Reality State — never from the latest message alone.";
exports.CARE_REALITY_STATE_CHAIN = [
    "input",
    "capture",
    "care_context",
    "active_care_situation",
    "progressive_understanding",
    "care_reality_state",
    "living_care_record",
    "timeline",
];
exports.CARE_REALITY_DISCLOSURE_STAGES = [
    "early",
    "growing",
    "established",
];
exports.CARE_REALITY_INTERNAL_QUESTION = "How has our understanding of this person's care reality changed?";
exports.CARE_REALITY_FORBIDDEN_INTERNAL_QUESTION = "What did the caregiver just type?";
exports.CARE_REALITY_STATE_NEVER = [
    "response_from_latest_message_alone",
    "treat_as_another_note",
    "treat_as_another_event",
    "freeze_understanding_permanently",
    "expose_all_sections_before_evidence",
];
/** Progressive disclosure — Living Care Record stores all; UI reveals by stage. */
exports.DISCLOSURE_SECTIONS_BY_STAGE = {
    early: [
        "confirmation",
        "current_understanding",
        "safety_ask_if_warranted",
    ],
    growing: [
        "confirmation",
        "what_changed",
        "current_understanding",
        "what_matters_now",
        "safety_ask_if_warranted",
    ],
    established: [
        "confirmation",
        "what_changed",
        "situation_summary",
        "pattern",
        "what_matters_now",
        "safety_ask_if_warranted",
        "what_will_be_remembered",
        "evidence_optional",
    ],
};
/** One primary question per screen (cognitive load budget). */
exports.COGNITIVE_LOAD_PRIMARY_QUESTIONS = [
    "Did solenos understand what happened?",
    "What has changed?",
    "What should I pay attention to?",
    "What information is still missing?",
];
