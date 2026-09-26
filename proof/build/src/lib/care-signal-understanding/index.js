"use strict";
/**
 * Care Signal Understanding Layer — generalized reasoning facade.
 *
 * Pipeline (never task-manager):
 *   Caregiver input → Care signals → Care state → What matters now → Missing context
 *
 * Engine-only. Never expose "care signal", category enums, or scores in caregiver UI.
 * Doc examples are illustrations only — never scenario if-branches.
 *
 * SoT: docs/02-product/solenos-care-signal-understanding.md
 */
Object.defineProperty(exports, "__esModule", { value: true });
exports.CARE_SIGNAL_UI_LEAKAGE_PATTERNS = exports.CARE_SIGNAL_UNDERSTANDING_REJECTS = exports.CARE_SIGNAL_UNDERSTANDING_PIPELINE = exports.CARE_SIGNAL_UNDERSTANDING_PURPOSE = void 0;
exports.containsCareSignalUiLeakage = containsCareSignalUiLeakage;
exports.preserveRawCaregiverInput = preserveRawCaregiverInput;
exports.processCareSignalUnderstanding = processCareSignalUnderstanding;
exports.caregiverFacingCareSignalUnderstanding = caregiverFacingCareSignalUnderstanding;
const care_reality_extraction_1 = require("../care-reality-extraction");
const clinical_situation_classification_1 = require("../care-reality-intelligence/clinical-situation-classification");
const unknowns_1 = require("../care-reality-extraction/unknowns");
exports.CARE_SIGNAL_UNDERSTANDING_PURPOSE = "Interpret caregiver input as fragments of a person's care reality — never as a task list.";
exports.CARE_SIGNAL_UNDERSTANDING_PIPELINE = [
    "preserve_raw_input",
    "infer_care_signals",
    "update_care_state_understanding",
    "what_matters_now",
    "missing_context",
];
exports.CARE_SIGNAL_UNDERSTANDING_REJECTS = [
    "input_to_task_checklist",
    "generic_chatbot",
    "reminder_app",
    "medical_diagnosis_system",
    "rewrite_or_replace_raw_input",
    "invent_missing_facts",
];
const DOMAIN_FROM_CATEGORY = {
    cognitive_change: "health_medical",
    behavioral_change: "health_medical",
    safety_concern: "health_medical",
    medication_transition: "health_medical",
    functional_decline: "daily_living",
    nutrition_hydration_change: "daily_living",
    sleep_change: "daily_living",
    caregiver_strain: "caregiver_load",
    family_coordination: "administrative_coordination",
    administrative_burden: "administrative_coordination",
};
/** Forbidden in any caregiver-facing string from this layer. */
exports.CARE_SIGNAL_UI_LEAKAGE_PATTERNS = [
    /\bcare signal\b/i,
    /\bsignal domains?\b/i,
    /\btask list\b/i,
    /\bchecklist of tasks\b/i,
    /\bthings to do:\b/i,
    /\bclinical category\b/i,
];
function containsCareSignalUiLeakage(blob) {
    return exports.CARE_SIGNAL_UI_LEAKAGE_PATTERNS.some((p) => p.test(blob));
}
/**
 * Preserve original caregiver expression exactly — no summarize/rewrite.
 */
function preserveRawCaregiverInput(raw) {
    return typeof raw === "string" ? raw : "";
}
function domainsFromClinical(clinical) {
    const set = new Set();
    for (const h of clinical.hits) {
        set.add(DOMAIN_FROM_CATEGORY[h.category] ?? "unspecified");
    }
    if (set.size === 0)
        set.add("unspecified");
    return [...set];
}
function humanPriorityFocus(clinical) {
    if (clinical.human_orientation?.trim())
        return clinical.human_orientation.trim();
    if (clinical.priority_focus === "safety_concern") {
        return "Recent changes that may affect safety deserve attention first.";
    }
    if (clinical.priority_focus === "medication_transition") {
        return "A medication-related change appears to need clearer understanding.";
    }
    if (clinical.primary.length > 0) {
        return "What changed recently for the person receiving care matters most right now.";
    }
    return null;
}
function burdenContext(clinical) {
    const loadHit = clinical.hits.find((h) => h.category === "caregiver_strain" || h.category === "family_coordination");
    if (!loadHit)
        return null;
    return "Keeping track of several moving pieces is part of the care load — without labeling or scoring the caregiver.";
}
/**
 * Core facade: raw input → care-reality understanding (not tasks).
 */
function processCareSignalUnderstanding(params) {
    const raw_input_preserved = preserveRawCaregiverInput(params.raw_input);
    const trimmed = raw_input_preserved.trim();
    const clinical = (0, clinical_situation_classification_1.classifyClinicalSituations)({ rawText: trimmed });
    const extraction = (0, care_reality_extraction_1.extractCareRealityFromText)({
        rawText: trimmed,
        contributorId: params.contributor_id,
        source: "caregiver",
    });
    const known = [];
    for (const o of extraction.observations.slice(0, 5)) {
        known.push(o.description);
    }
    for (const e of extraction.events.slice(0, 3)) {
        known.push(e.description);
    }
    for (const d of extraction.decisions.slice(0, 3)) {
        known.push(d.reason_unknown
            ? `${d.description} (reason unknown)`
            : d.why
                ? `${d.description} — ${d.why}`
                : d.description);
    }
    for (const a of extraction.actions.slice(0, 3)) {
        known.push(a.description);
    }
    const uncertain = [];
    for (const u of extraction.unknowns.slice(0, 5)) {
        if (u.status === "open")
            uncertain.push(u.question);
    }
    // Structural gaps when decisions lack why
    for (const d of extraction.decisions) {
        if (d.reason_unknown) {
            const q = "Why this care decision was made remains unclear.";
            if (!uncertain.includes(q))
                uncertain.push(q);
        }
    }
    const what_would_improve_understanding = [];
    for (const u of uncertain.slice(0, 2)) {
        what_would_improve_understanding.push(u);
    }
    if (what_would_improve_understanding.length < 2 &&
        extraction.observations.length > 0 &&
        extraction.decisions.length === 0) {
        what_would_improve_understanding.push("Whether this is different from the person's usual pattern.");
    }
    const care_state_understanding = clinical.human_orientation?.trim() ||
        (known.length > 0
            ? "This input adds to the current understanding of the person's care situation."
            : trimmed.length > 0
                ? "This input is held as part of the care story; more context would sharpen understanding."
                : null);
    const what_matters_now = humanPriorityFocus(clinical);
    const result = {
        raw_input_preserved,
        signal_domains: domainsFromClinical(clinical),
        clinical,
        care_state_understanding,
        known: known.slice(0, 8),
        uncertain: uncertain.slice(0, 6),
        what_would_improve_understanding: what_would_improve_understanding.slice(0, 3),
        what_matters_now,
        caregiver_burden_context: burdenContext(clinical),
        rejects_task_pipeline: true,
        extraction_summary: {
            observations: extraction.observations.length,
            events: extraction.events.length,
            decisions: extraction.decisions.length,
            actions: extraction.actions.length,
            outcomes: extraction.outcomes.length,
            unknowns: extraction.unknowns.length,
        },
    };
    // Guard: never invent certainty from uncertainty in caregiver-facing fields
    const face = [
        result.care_state_understanding,
        result.what_matters_now,
        ...result.known,
        ...result.uncertain,
    ]
        .filter(Boolean)
        .join("\n");
    if ((0, clinical_situation_classification_1.containsClinicalCategoryLeakage)(face) || containsCareSignalUiLeakage(face)) {
        result.care_state_understanding =
            "This input is held as part of the person's care reality.";
        result.what_matters_now =
            "Clarify what changed recently for the person receiving care.";
    }
    if ((0, unknowns_1.looksLikeInventedCertaintyFromUncertainty)(face) && result.uncertain.length > 0) {
        // Prefer uncertainty over false certainty in matters-now
        if (result.what_matters_now && /definitely|caused by|certainly/i.test(result.what_matters_now)) {
            result.what_matters_now = result.uncertain[0] ?? result.what_matters_now;
        }
    }
    return result;
}
/**
 * Caregiver-facing projection — never leaks engine jargon.
 */
function caregiverFacingCareSignalUnderstanding(result) {
    return {
        what_appears_happening: result.care_state_understanding,
        what_matters_now: result.what_matters_now,
        what_is_unclear: result.uncertain,
        what_to_ask_next: result.what_would_improve_understanding,
        raw_input_preserved: result.raw_input_preserved,
    };
}
