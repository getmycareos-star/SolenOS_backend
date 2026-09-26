"use strict";
/**
 * Generalized Care Understanding Rules — intelligence behaviors, not keyword products.
 *
 * Caregiver language → Meaning → Care signals → Current care understanding → Next understanding
 *
 * Doc examples are illustrations only. Never hard-code pharmacy/food/fall/med if-branches.
 *
 * SoT: docs/02-product/solenos-generalized-care-understanding.md
 */
Object.defineProperty(exports, "__esModule", { value: true });
exports.GENERALIZED_CARE_UNDERSTANDING_RULES = exports.GENERALIZED_CARE_UNDERSTANDING_PIPELINE = exports.GENERALIZED_CARE_UNDERSTANDING_PURPOSE = exports.processAdditionalIntelligenceBehaviors = exports.ADDITIONAL_INTELLIGENCE_BEHAVIORS = void 0;
exports.processGeneralizedCareUnderstanding = processGeneralizedCareUnderstanding;
exports.caregiverFacingGeneralizedUnderstanding = caregiverFacingGeneralizedUnderstanding;
exports.presentsDerivedAsObservedFact = presentsDerivedAsObservedFact;
const care_reality_extraction_1 = require("../care-reality-extraction");
const care_epistemics_1 = require("../care-epistemics");
const classify_1 = require("../care-reality-extraction/classify");
const care_signal_understanding_1 = require("../care-signal-understanding");
const additional_behaviors_1 = require("./additional-behaviors");
var additional_behaviors_2 = require("./additional-behaviors");
Object.defineProperty(exports, "ADDITIONAL_INTELLIGENCE_BEHAVIORS", { enumerable: true, get: function () { return additional_behaviors_2.ADDITIONAL_INTELLIGENCE_BEHAVIORS; } });
Object.defineProperty(exports, "processAdditionalIntelligenceBehaviors", { enumerable: true, get: function () { return additional_behaviors_2.processAdditionalIntelligenceBehaviors; } });
exports.GENERALIZED_CARE_UNDERSTANDING_PURPOSE = "Care understanding engine — meaning over keywords; never a task extractor.";
exports.GENERALIZED_CARE_UNDERSTANDING_PIPELINE = [
    "caregiver_language",
    "meaning",
    "care_signals",
    "current_care_understanding",
    "appropriate_next_steps",
];
/** The ten locked rules — behavioral contracts, not scenario templates. */
exports.GENERALIZED_CARE_UNDERSTANDING_RULES = [
    "semantic_understanding_over_keyword_matching",
    "extract_care_reality_signals_as_concepts",
    "build_and_update_living_care_state",
    "identify_importance_dynamically",
    "detect_what_can_wait",
    "generate_follow_up_questions_intelligently",
    "maintain_open_loops",
    "connect_information_across_time",
    "separate_reality_from_interpretation",
    "preserve_human_context_without_therapy_chatbot",
];
function askChangesNextStep(question) {
    // Structural: clarifying safety/change/decision/outcome — not keyword product lists.
    const q = question.toLowerCase();
    if (q.length < 8)
        return false;
    if (/\b(?:why|whether|when|who|what)\b/.test(q))
        return true;
    if (/\b(?:unclear|unknown|missing|confirm)\b/.test(q))
        return true;
    return question.trim().endsWith("?");
}
/**
 * Generalized understanding pass for one caregiver input.
 * Composes extraction + care-signal understanding — adds epistemic bands + open loops.
 */
function processGeneralizedCareUnderstanding(params) {
    const raw = typeof params.raw_input === "string" ? params.raw_input : "";
    const csl = (0, care_signal_understanding_1.processCareSignalUnderstanding)({
        raw_input: raw,
        contributor_id: params.contributor_id,
    });
    const extraction = (0, care_reality_extraction_1.extractCareRealityFromText)({
        rawText: raw,
        contributorId: params.contributor_id,
        source: "caregiver",
    });
    const signal_concepts = [];
    if (extraction.events.length > 0)
        signal_concepts.push("event");
    if (extraction.observations.length > 0)
        signal_concepts.push("observation");
    if (extraction.decisions.length > 0)
        signal_concepts.push("decision");
    if (extraction.unknowns.some((u) => u.status === "open")) {
        signal_concepts.push("unknown");
    }
    if (extraction.outcomes.length > 0 || csl.what_matters_now) {
        signal_concepts.push("change");
    }
    if ((0, classify_1.looksLikeContributorLoadFragment)(raw) || csl.caregiver_burden_context) {
        signal_concepts.push("responsibility");
        signal_concepts.push("concern");
    }
    if (csl.signal_domains.includes("caregiver_load") && !signal_concepts.includes("concern")) {
        signal_concepts.push("concern");
    }
    const observed = [];
    const derived = [];
    const unknown = [];
    for (const o of extraction.observations.slice(0, 6)) {
        const claim = (0, care_epistemics_1.classifyEpistemicClaim)(o.raw_fragment || o.description);
        if (claim === "caregiver_interpretation") {
            derived.push(o.description);
        }
        else {
            observed.push(o.description);
        }
    }
    for (const e of extraction.events.slice(0, 4)) {
        observed.push(e.description);
    }
    for (const d of extraction.decisions.slice(0, 4)) {
        observed.push(d.description);
        if (d.reason_unknown) {
            unknown.push("Why this care decision was made remains unclear.");
        }
        else if (d.why) {
            // Stated why is reported; still not a clinical conclusion.
            observed.push(`Stated reason held: ${d.why}`);
        }
    }
    for (const a of extraction.actions.slice(0, 3)) {
        observed.push(a.description);
    }
    for (const u of extraction.unknowns) {
        if (u.status === "open")
            unknown.push(u.question);
    }
    for (const u of csl.uncertain) {
        if (!unknown.includes(u))
            unknown.push(u);
    }
    // Derived: care-state orientation is inference — never merge into observed.
    if (csl.care_state_understanding) {
        derived.push(csl.care_state_understanding);
    }
    if (csl.what_matters_now) {
        derived.push(csl.what_matters_now);
    }
    const prior = params.prior_held ?? [];
    const longitudinalHint = prior.length > 0
        ? "This input is evaluated against what is already held in the care story."
        : "This input begins or extends the care story for this person.";
    const open_loops = [];
    for (const q of unknown.slice(0, 5)) {
        if (!askChangesNextStep(q))
            continue;
        open_loops.push({
            question: q,
            why_it_matters: "Closing this gap would sharpen care understanding.",
            status: "open",
        });
    }
    for (const d of extraction.decisions) {
        if (d.reason_unknown && d.outcome == null) {
            open_loops.push({
                question: "Outcome after this care decision is not yet known.",
                why_it_matters: "Decisions without outcomes leave the care journey incomplete.",
                status: "open",
            });
        }
    }
    const followUps = csl.what_would_improve_understanding
        .filter((q) => askChangesNextStep(q))
        .slice(0, 3);
    const requires_attention_now = csl.what_matters_now;
    const worth_following_up = followUps;
    const useful_background = observed.slice(0, 2);
    const caregiver_capacity_context = csl.caregiver_burden_context ??
        ((0, classify_1.looksLikeContributorLoadFragment)(raw)
            ? "Caregiver load is context for capacity — not a diagnosis or score."
            : null);
    const baseFields = {
        raw_input_preserved: csl.raw_input_preserved,
        rules_applied: exports.GENERALIZED_CARE_UNDERSTANDING_RULES,
        signal_concepts: [...new Set(signal_concepts)],
        epistemic: {
            observed: observed.slice(0, 8),
            derived: derived.slice(0, 6),
            unknown: unknown.slice(0, 6),
        },
        internal: {
            information_provided: [...observed.slice(0, 5), ...derived.slice(0, 2)],
            reveals_about_care_situation: csl.care_state_understanding,
            what_changed: extraction.outcomes[0]?.description ??
                (prior.length > 0 ? "Compared with what was already held." : null),
            what_is_important: requires_attention_now,
            what_is_uncertain: unknown.slice(0, 5),
            what_remains_in_memory: [
                ...observed.slice(0, 4),
                ...extraction.decisions.map((d) => d.description).slice(0, 2),
            ],
            what_needs_follow_up: worth_following_up,
            care_state_update: [longitudinalHint, csl.care_state_understanding]
                .filter(Boolean)
                .join(" "),
        },
        open_loops: open_loops.slice(0, 5),
        requires_attention_now,
        worth_following_up,
        useful_background,
        caregiver_capacity_context,
        is_care_understanding_engine: true,
    };
    const additional = (0, additional_behaviors_1.processAdditionalIntelligenceBehaviors)({
        raw_input: raw,
        contributor_id: params.contributor_id,
        prior_held: prior,
        base: baseFields,
        extraction,
        care_signal: csl,
    });
    // Decision readiness: prefer clarifying missing info over premature next steps
    if (!additional.decision_readiness.ready &&
        additional.decision_readiness.missing_first.length > 0) {
        baseFields.worth_following_up = additional.decision_readiness.missing_first.slice(0, 3);
        baseFields.internal.what_needs_follow_up = baseFields.worth_following_up;
    }
    // Cognitive overload: keep primary focus; demote background
    if (additional.cognitive_overload.primary_focus) {
        baseFields.requires_attention_now = additional.cognitive_overload.primary_focus;
        baseFields.internal.what_is_important = additional.cognitive_overload.primary_focus;
    }
    // Contradiction: surface as unknown/open loop — never overwrite prior
    if (additional.contradiction.detected && additional.contradiction.current_interpretation) {
        if (!baseFields.epistemic.unknown.includes(additional.contradiction.current_interpretation)) {
            baseFields.epistemic.unknown = [
                additional.contradiction.current_interpretation,
                ...baseFields.epistemic.unknown,
            ].slice(0, 6);
        }
    }
    return {
        ...baseFields,
        additional,
    };
}
/** Caregiver-facing projection — never leaks Observed/Derived labels as chrome. */
function caregiverFacingGeneralizedUnderstanding(result) {
    return {
        what_appears_happening: result.internal.reveals_about_care_situation,
        what_matters_now: result.requires_attention_now,
        what_is_unclear: result.epistemic.unknown,
        what_can_wait: result.useful_background.length > 0
            ? "Background details that do not change what needs attention first."
            : null,
        raw_input_preserved: result.raw_input_preserved,
    };
}
function presentsDerivedAsObservedFact(blob) {
    return (/\b(?:definitely|certainly|caused by|is because)\b/i.test(blob) &&
        /\b(?:unknown|unclear|not sure)\b/i.test(blob));
}
