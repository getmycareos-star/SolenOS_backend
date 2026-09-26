"use strict";
/** Moment-of-Need Engine — transform uncertainty into understanding during the difficult moment. */
Object.defineProperty(exports, "__esModule", { value: true });
exports.MOMENT_TRIGGER_PATTERNS = exports.TRACKING_QUESTIONS = exports.CHANGE_TYPE_LABELS = exports.CARE_MOMENT_FUTURE_REF = exports.MOMENT_OF_NEED_PROHIBITED = exports.MOMENT_OF_NEED_RULES = exports.MOMENT_OF_NEED_SECTIONS = exports.HELPLESSNESS_REDUCTION_GOAL = exports.MOMENT_OF_NEED_DEFINING_PRINCIPLE = exports.MOMENT_OF_NEED_IDENTITY = void 0;
exports.MOMENT_OF_NEED_IDENTITY = "Help me understand what is happening right now.";
exports.MOMENT_OF_NEED_DEFINING_PRINCIPLE = "Caregivers need support during the difficult moment — not after it.";
exports.HELPLESSNESS_REDUCTION_GOAL = "Reduce helplessness through better understanding — not organization.";
exports.MOMENT_OF_NEED_SECTIONS = [
    "what_changed",
    "what_we_know",
    "possible_context",
    "questions_worth_tracking",
];
exports.MOMENT_OF_NEED_RULES = [
    "connect_to_existing_care_reality",
    "show_evidence_not_diagnosis",
    "surface_uncertainty_explicitly",
    "never_recommend_medical_treatment",
    "never_claim_certainty_without_evidence",
    "reduce_interpretation_burden",
    "human_support_when_appropriate",
];
exports.MOMENT_OF_NEED_PROHIBITED = [
    "diagnose conditions",
    "recommend medical treatment",
    "replace clinicians",
    "generic dementia FAQ responses",
    "symptom checker behavior",
    "claim certainty where evidence is incomplete",
    "ask anything chatbot UX",
];
/** Future Care Moment capability — Phase 2; see future-capabilities/care-moment.ts */
exports.CARE_MOMENT_FUTURE_REF = "src/lib/future-capabilities/care-moment";
exports.CHANGE_TYPE_LABELS = {
    new_observation: "New observation compared with prior record",
    repeated_pattern: "Repeated pattern in care history",
    escalation: "Escalation from prior baseline",
    return_of_previous: "Return of a previously recorded issue",
};
exports.TRACKING_QUESTIONS = [
    "When did this begin?",
    "Does it happen at a specific time of day?",
    "Is anything different on those days?",
    "Has this happened before?",
    "What responses have helped previously?",
];
/** Patterns that indicate a real-time moment-of-need input */
exports.MOMENT_TRIGGER_PATTERNS = [
    /\b(today|right now|this morning|this evening|just now|keeps?|again today)\b/i,
    /\b(noticed|happening|started|refused|won't|can't|confused|asking)\b/i,
];
