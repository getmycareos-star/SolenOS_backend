"use strict";
/** Baseline Intelligence — is this different for this person? */
Object.defineProperty(exports, "__esModule", { value: true });
exports.BASELINE_MIN_OBSERVATIONS = exports.BASELINE_PROHIBITED = exports.BASELINE_INTELLIGENCE_RULES = exports.BASELINE_PATTERNS = exports.BASELINE_DOMAINS = exports.BASELINE_INTELLIGENCE_DEFINING_PRINCIPLE = exports.BASELINE_INTELLIGENCE_IDENTITY = void 0;
exports.BASELINE_INTELLIGENCE_IDENTITY = "SolenOS helps answer: what is happening with this person, compared with what we know about them?";
exports.BASELINE_INTELLIGENCE_DEFINING_PRINCIPLE = "The meaning of a change depends on the person's own history — not generic medical categories.";
exports.BASELINE_DOMAINS = [
    "routine",
    "communication",
    "sleep",
    "appetite",
    "mobility",
    "mood",
    "medication_adherence",
    "social",
];
exports.BASELINE_PATTERNS = [
    { domain: "routine", pattern: /\b(routine|morning|evening|daily|habit|usually|normally|schedule|pattern)\b/i },
    { domain: "communication", pattern: /\b(ask(?:ing|s)?|repeat(?:ing|s)?|question|talk|conversation|confus(?:ed|ion)?|word|name)\b/i },
    { domain: "sleep", pattern: /\b(sleep|nap|night|insomnia|restless|awake|tired|exhausted|woke up|up all night)\b/i },
    { domain: "appetite", pattern: /\b(appetite|eat(?:ing|s)?|meal|breakfast|lunch|dinner|refus(?:ed|es)?|hungry|not eating|skipped meal)\b/i },
    { domain: "mobility", pattern: /\b(walk(?:ing|s)?|mobility|fall|unsteady|wheelchair|transfer|balance|fell|got up|moving around)\b/i },
    { domain: "mood", pattern: /\b(upset|agitat(?:ed|ion)?|anxious|calm|mood|irritable|happy|sad|frustrated|angry|crying|tears|emotional)\b/i },
    { domain: "medication_adherence", pattern: /\b(medication|med|pill|dose|refus(?:ed|es)?|took|taken|missed|forgot|adherence|refused|giving medication)\b/i },
    { domain: "social", pattern: /\b(visit|visitor|family|friend|lonely|withdrawn|social|alone|isolated|talking to|calling)\b/i },
];
exports.BASELINE_INTELLIGENCE_RULES = [
    "compare_against_person_history",
    "never_generic_diagnosis",
    "never_symptom_encyclopedia",
    "surface_deviation_not_category",
    "preserve_uncertainty",
    "evidence_linked_to_events",
];
exports.BASELINE_PROHIBITED = [
    "generic dementia education",
    "symptom encyclopedia responses",
    "medical diagnosis from baseline deviation",
    "category-based advice without person history",
];
/** Minimum prior observations before baseline is considered established */
exports.BASELINE_MIN_OBSERVATIONS = 2;
