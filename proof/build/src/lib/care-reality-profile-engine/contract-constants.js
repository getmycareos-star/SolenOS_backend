"use strict";
/** Care Reality Profile — person-specific understanding, not generic categories. */
Object.defineProperty(exports, "__esModule", { value: true });
exports.DECISION_PATTERNS = exports.DID_NOT_HELP_PATTERNS = exports.HELPED_PATTERNS = exports.ROUTINE_PATTERNS = exports.HUMAN_CONTEXT_LAYER_REF = exports.CARE_REALITY_PROFILE_RULES = exports.MEMORY_EVOLUTION_STAGES = exports.PROFILE_SECTIONS = exports.CARE_REALITY_PROFILE_DEFINING_PRINCIPLE = exports.CARE_REALITY_PROFILE_IDENTITY = void 0;
exports.CARE_REALITY_PROFILE_IDENTITY = "SolenOS knows this person — not just a condition category.";
exports.CARE_REALITY_PROFILE_DEFINING_PRINCIPLE = "The Living Care Record accumulates context: fact → context → pattern → learning → understanding.";
exports.PROFILE_SECTIONS = [
    "baseline_reality",
    "important_routines",
    "known_changes",
    "previous_decisions",
    "what_helped",
    "what_did_not_help",
    "family_observations",
    "unresolved_questions",
];
exports.MEMORY_EVOLUTION_STAGES = [
    "fact",
    "context",
    "pattern",
    "learning",
    "understanding",
];
exports.CARE_REALITY_PROFILE_RULES = [
    "person_specific_not_generic",
    "relationships_over_isolated_facts",
    "preserve_decision_context",
    "track_outcomes",
    "maintain_unresolved_questions",
    "never_diagnose_from_profile",
    "preserve_human_context_not_condition_labels",
];
/** Person-specific human context — see future-capabilities/human-context.ts */
exports.HUMAN_CONTEXT_LAYER_REF = "src/lib/future-capabilities/human-context";
exports.ROUTINE_PATTERNS = [
    /\b(morning routine|breakfast|medication time|evening|bedtime|appointment)\b/i,
    /\b(daily|every day|usually|typically|always)\b/i,
];
exports.HELPED_PATTERNS = [
    /\b(helped|improved|calmed|worked|better after|reduced when)\b/i,
];
exports.DID_NOT_HELP_PATTERNS = [
    /\b(did not help|didn't help|no improvement|failed|unsuccessful|made worse)\b/i,
];
exports.DECISION_PATTERNS = [
    /\b(decided|changed|adjusted|switched|because|due to|as a result)\b/i,
];
