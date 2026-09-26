"use strict";
/**
 * Initial Care Reality Assessment Mode — Architecture 2B.
 * When no comparable prior exists: build first understanding — never hallucinate change.
 *
 * SoT: docs/02-product/solenos-initial-care-reality-assessment.md
 * Doc examples are illustrations only — never product if-branches on scenario nouns.
 */
Object.defineProperty(exports, "__esModule", { value: true });
exports.PERSON_BASELINE_AREAS = exports.HALLUCINATED_CHANGE_PATTERNS = exports.INITIAL_CARE_REALITY_ASSESSMENT_PURPOSE = void 0;
exports.containsHallucinatedChangeLanguage = containsHallucinatedChangeLanguage;
exports.initialBaselineEstablishmentAsks = initialBaselineEstablishmentAsks;
exports.orientationFromInitialAssessment = orientationFromInitialAssessment;
exports.orientationFromComparisonInitialMode = orientationFromComparisonInitialMode;
exports.INITIAL_CARE_REALITY_ASSESSMENT_PURPOSE = "When no comparable prior exists, build first understanding of current care reality — never invent change from a past that is not held.";
/**
 * Caregiver-facing phrases that invent change/history without a comparable prior.
 * Engine + acceptance gate — never product templates for happy-path copy.
 */
exports.HALLUCINATED_CHANGE_PATTERNS = [
    /\bthis is different from before\b/i,
    /\bdifferent from (?:her|his|their|the) previous\b/i,
    /\bdiffer(?:s|ed)? from (?:her|his|their) previous pattern\b/i,
    /\bappears to be a decline\b/i,
    /\bthis is a (?:new|novel) behavior\b/i,
    /\bgetting worse\b/i,
    /\bcondition appears to be getting worse\b/i,
    /\brecent changes in .{0,40}care reality\b/i,
    /\bshift from .{0,40}previous (?:pattern|routine|level)\b/i,
    /\bcompared with what was usual before\b/i,
    /\bwhat used to be part of .{0,40}usual pattern appears different\b/i,
];
function containsHallucinatedChangeLanguage(blob) {
    return exports.HALLUCINATED_CHANGE_PATTERNS.some((p) => p.test(blob));
}
/** Soft asks that grow person baseline — never a form wall. */
function initialBaselineEstablishmentAsks(params) {
    const who = params.person && params.person !== "they" ? params.person : null;
    const momLike = who && /^(mom|mum|mother)$/i.test(who);
    const forWho = momLike ? "your mom" : who ? who : "them";
    return [
        `Before these concerns, what was a normal day like for ${forWho}?`,
        "When did you first notice these differences?",
        "Has anything happened recently (hospital visit, medication change, illness, major event)?",
    ];
}
/**
 * Orientation for Initial Care Reality Assessment Mode.
 * Speaks current situation + known/unknown — invites baseline, never claims change.
 */
function orientationFromInitialAssessment(params) {
    const who = params.person && params.person !== "they" ? params.person : null;
    const concerns = params.current_concerns.slice(0, 4);
    const asks = initialBaselineEstablishmentAsks({ person: who });
    let current_understanding = null;
    if (concerns.length > 0) {
        const whoLine = who
            ? `We understand that ${who} has current care concerns held from what you shared.`
            : "We understand current care concerns from what you shared.";
        const joined = concerns.map((c) => c.replace(/\.$/, "")).join("; ");
        // Never dump near-raw multi-clause captures into orientation.
        const concernList = concerns.length >= 2 ||
            joined.length > 90 ||
            concerns.some((c) => c.length > 70 || /,\s*| and (?:i|she|he|they)\b/i.test(c))
            ? "how they are feeling, a care visit or care moment, and daily patterns that look harder lately"
            : joined;
        current_understanding = `${whoLine} Held so far: ${concernList}. To understand whether this represents a change from the usual pattern, we need to establish what was normal before.`;
    }
    else if (params.known_facts[0]) {
        current_understanding = `What we understand so far: ${params.known_facts[0].replace(/\.$/, "")}. What was usual before is not held yet.`;
    }
    const still_unclear = [
        "Whether this is different from the usual pattern",
        "When these concerns started",
        "What a normal day looked like before",
    ].slice(0, 3);
    // what_changed is null in initial assessment — no comparable prior
    return {
        current_understanding,
        what_changed: null,
        still_unclear,
        one_thing_to_add: asks[0] ?? null,
        mode: "initial_assessment",
    };
}
/**
 * Build initial-assessment orientation from a baseline comparison result in that mode.
 */
function orientationFromComparisonInitialMode(comparison) {
    return orientationFromInitialAssessment({
        person: comparison.person,
        current_concerns: comparison.current_concerns,
        known_facts: comparison.known_facts,
        related_context: comparison.related_context,
    });
}
/** Person baseline areas to grow over time — engine checklist, never UI form. */
exports.PERSON_BASELINE_AREAS = [
    "identity",
    "daily_life",
    "abilities",
    "preferences",
    "health_context",
    "behavior_patterns",
    "care_environment",
];
