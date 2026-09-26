"use strict";
/** Architectural Boundaries — non-negotiable invariants for every component. */
Object.defineProperty(exports, "__esModule", { value: true });
exports.COMPONENTS_UNDER_BOUNDARY = exports.SAFE_ALTERNATIVES = exports.ENGAGEMENT_VIOLATION_PATTERNS = exports.INVENTED_CERTAINTY_PATTERNS = exports.DIAGNOSIS_VIOLATION_PATTERNS = exports.DECISION_FRAMEWORK_QUESTIONS = exports.RULE_DEFINITIONS = exports.ARCHITECTURAL_RULES = exports.BOUNDARIES_DEFINING_PRINCIPLE = exports.BOUNDARIES_IDENTITY = void 0;
exports.BOUNDARIES_IDENTITY = "The long-term value of solenos depends on trust, not apparent intelligence.";
exports.BOUNDARIES_DEFINING_PRINCIPLE = "Every architectural decision should favor accuracy over certainty, continuity over convenience, transparency over persuasion, and caregiver wellbeing over engagement.";
exports.ARCHITECTURAL_RULES = [
    "never_diagnose",
    "never_replace_clinical_judgment",
    "never_invent_information",
    "never_hide_uncertainty",
    "never_overwrite_history",
    "never_pretend_confidence",
    "never_optimize_for_engagement",
    "never_separate_observations_from_evidence",
    "never_destroy_continuity",
    "never_prioritize_automation_over_accuracy",
];
exports.RULE_DEFINITIONS = {
    never_diagnose: "Never diagnose diseases, conditions, or emergencies — organize observations and suggest follow-up only.",
    never_replace_clinical_judgment: "Healthcare decisions belong to qualified professionals — solenos improves continuity, not clinical authority.",
    never_invent_information: "Missing information remains missing — never fabricate symptoms, medications, dates, or explanations.",
    never_hide_uncertainty: "Uncertainty is first-class — every inference communicates confidence, assumptions, and alternatives.",
    never_overwrite_history: "Record transitions rather than replacing previous states — the timeline is part of the product.",
    never_pretend_confidence: "Confidence is earned through evidence — never inflated to appear authoritative.",
    never_optimize_for_engagement: "No unnecessary conversations, notifications, or screen-time optimization — reduce burden only.",
    never_separate_observations_from_evidence: "Every conclusion traceable to CareEvents, observations, and evidence sources.",
    never_destroy_continuity: "Information accumulates — context deepens — history is never lost for convenience.",
    never_prioritize_automation_over_accuracy: "When uncertainty is too high, ask, defer, or preserve unknowns — never assume.",
};
exports.DECISION_FRAMEWORK_QUESTIONS = [
    "Does this preserve truth?",
    "Does this reduce uncertainty rather than conceal it?",
    "Does this strengthen continuity?",
    "Can every recommendation be explained?",
    "Is confidence proportional to available evidence?",
    "Does this reduce caregiver cognitive load without replacing clinical judgment?",
];
/** Prohibited output patterns — diagnosis and invented certainty. */
exports.DIAGNOSIS_VIOLATION_PATTERNS = [
    /\b(he|she|they|patient)\s+has\s+(delirium|pneumonia|uti|infection|dementia|alzheimer|stroke|sepsis)\b/i,
    /\bthis\s+is\s+(delirium|pneumonia|a\s+uti|an?\s+infection|dementia)\b/i,
    /\b(diagnosed\s+with|diagnosis\s+is|confirmed\s+diagnosis\s+of)\b/i,
    /\b(likely\s+has|probably\s+has|definitely\s+has)\s+\w+\s+(disease|disorder|syndrome|condition)\b/i,
    /\bstage\s+[0-4]\s+dementia\b/i,
];
exports.INVENTED_CERTAINTY_PATTERNS = [
    /\bdefinitely\s+(has|is|will|was)\b/i,
    /\bwithout\s+(a\s+)?doubt\b/i,
    /\bcertainly\s+(has|is|caused\s+by)\b/i,
    /\bconfirmed\s+(that|diagnosis)\b/i,
    /\bwe\s+know\s+(for\s+sure|with\s+certainty)\b/i,
];
exports.ENGAGEMENT_VIOLATION_PATTERNS = [
    /\bcheck\s+in\s+daily\b/i,
    /\bopen\s+the\s+app\s+(every|each)\b/i,
    /\bdon't\s+forget\s+to\s+log\b/i,
    /\bstreak\b/i,
    /\bgamif/i,
];
exports.SAFE_ALTERNATIVES = [
    {
        pattern: /\b(he|she|they)\s+has\s+delirium\b/i,
        replacement: "These observations may warrant medical evaluation.",
    },
    {
        pattern: /\bthis\s+is\s+pneumonia\b/i,
        replacement: "These observations may warrant medical evaluation.",
    },
];
exports.COMPONENTS_UNDER_BOUNDARY = [
    "situation_parser",
    "care_event_engine",
    "care_context_manager",
    "behavior_interpretation_engine",
    "prioritization_engine",
    "pattern_learning_engine",
    "clarification_engine",
    "explainability_engine",
    "apis",
    "database",
    "user_interface",
];
