"use strict";
/** Crisis Mode Interaction Layer — triage assistant, not chatbot. */
Object.defineProperty(exports, "__esModule", { value: true });
exports.URGENT_INPUT_THRESHOLD = exports.URGENT_INPUT_WINDOW_MINUTES = exports.MAX_IMMEDIATE_CONCERNS = exports.MAX_LINES_PER_SECTION = exports.CRISIS_SUPPRESSED_ENGINES = exports.SOFT_URGENT_PATTERNS = exports.SOFT_HELP_PATTERNS = exports.CAREGIVER_DISTRESS_PATTERNS = exports.HIGH_SEVERITY_EVENT_PATTERNS = exports.CRISIS_BEHAVIOR_RULES = exports.CRISIS_URGENCY_LEVELS = exports.CRISIS_MODE_DEFINING_PRINCIPLE = exports.CRISIS_MODE_IDENTITY = void 0;
exports.CRISIS_MODE_IDENTITY = "In calm moments, SolenOS helps caregivers understand. In crisis moments, SolenOS tells caregivers what to do next.";
exports.CRISIS_MODE_DEFINING_PRINCIPLE = "As urgency increases, cognitive load must decrease.";
exports.CRISIS_URGENCY_LEVELS = ["low", "medium", "high", "critical"];
exports.CRISIS_BEHAVIOR_RULES = [
    "reduce_output_complexity",
    "prioritize_action_over_understanding",
    "suppress_non_essential_engines",
    "one_idea_per_line",
    "max_five_lines_per_section",
    "no_diagnosis_no_fabricated_certainty",
    "anchor_in_observed_behavior",
    "defer_non_critical_writes",
];
exports.HIGH_SEVERITY_EVENT_PATTERNS = [
    // Fall is gated separately (immediacy/severity required) — see fall-crisis-gate.ts
    { pattern: /\b(sudden confusion|confused suddenly|acute confusion)\b/i, label: "sudden confusion" },
    {
        pattern: /\b(refus\w+|won't|will not)\s+(food|fluid|eat|drink|water)\b/i,
        label: "refusal of food or fluids",
    },
    {
        pattern: /\b(refus\w+|won't|will not)\s+(medication|meds|pill|medicine)\b/i,
        label: "medication refusal",
    },
    { pattern: /\b(acute agitat|very agitat|extremely agitat|violent)\b/i, label: "acute agitation" },
];
/** Distress that can escalate alone — not bare "help me" / "urgent" / "right now". */
exports.CAREGIVER_DISTRESS_PATTERNS = [
    /\b(don't know what to do|i don't know what to do)\b/i,
    /\b(panicking|overwhelmed|can't cope)\b/i,
    /\bemergency\b/i,
];
/** Soft help/urgent tokens — only count when paired with acute context (see detect-triggers). */
exports.SOFT_HELP_PATTERNS = [
    /\b(help me|please help|need help)\b/i,
    /\bhelp!\b/i,
];
exports.SOFT_URGENT_PATTERNS = [
    // Match "urgent" but not the care setting "urgent care".
    /\burgent\b(?!\s+care\b)/i,
    /\bright now\b/i,
];
exports.CRISIS_SUPPRESSED_ENGINES = [
    "pattern_learning_engine",
    "long_term_analysis",
    "deep_graph_traversal",
    "detailed_explanations",
    "exploratory_analysis",
];
exports.MAX_LINES_PER_SECTION = 5;
exports.MAX_IMMEDIATE_CONCERNS = 3;
exports.URGENT_INPUT_WINDOW_MINUTES = 30;
exports.URGENT_INPUT_THRESHOLD = 2;
