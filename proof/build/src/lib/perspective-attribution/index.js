"use strict";
/**
 * Perspective attribution (G16) — who-said on shared Living Care Record.
 *
 * Not a caregiver chat feed. Surfaces distinct held views with optional
 * role labels when contributor ids encode family roles.
 * Private raw cross-user leak remains forbidden (multi-caregiver privacy).
 */
Object.defineProperty(exports, "__esModule", { value: true });
exports.PERSPECTIVE_ATTRIBUTION_PURPOSE = void 0;
exports.composePerspectiveAttribution = composePerspectiveAttribution;
const care_epistemics_1 = require("../care-epistemics");
exports.PERSPECTIVE_ATTRIBUTION_PURPOSE = "Organize disagreeing views with visible attribution — never silent winner, never chat feed.";
function roleLabel(contributorId) {
    if (!contributorId)
        return null;
    const id = contributorId.toLowerCase();
    if (/\bdaughter\b/.test(id))
        return "Daughter";
    if (/\bson\b/.test(id))
        return "Son";
    if (/\bbrother\b/.test(id))
        return "Brother";
    if (/\bsister\b/.test(id))
        return "Sister";
    if (/\bspouse|wife|husband\b/.test(id))
        return "Spouse";
    if (/\bnurse|aide|professional\b/.test(id))
        return "Care professional";
    return null;
}
function shortFact(o, priorFacts) {
    const fact = (0, care_epistemics_1.observationCareFact)({
        human_fact: o.human_fact,
        raw_text: o.raw_text,
        priorFacts,
    }) ?? (o.human_fact || o.raw_text).trim();
    return fact.slice(0, 100);
}
/**
 * Build caregiver-visible perspective lines when multiple contributors differ.
 * Both sides retained — never silent winner (Slice 5.4).
 */
function composePerspectiveAttribution(params) {
    const obs = params.situation.observations;
    const withIds = obs.filter((o) => o.contributor_id);
    const ids = [...new Set(withIds.map((o) => o.contributor_id))];
    const conflictPattern = params.patternLabel === "disagreeing care views" ||
        params.patternLabel === "source conflict";
    if (ids.length < 2 && !conflictPattern) {
        return {
            show: false,
            evidence_line: null,
            what_we_know_extra: [],
            silent_winner: false,
            both_retained: true,
        };
    }
    // Pick latest observation per contributor (sequential priorFacts for thin threads)
    const latestByContributor = new Map();
    const priorFacts = [];
    for (const o of obs) {
        const id = o.contributor_id ?? "unknown";
        latestByContributor.set(id, o);
        const fact = (0, care_epistemics_1.observationCareFact)({
            human_fact: o.human_fact,
            raw_text: o.raw_text,
            priorFacts,
        });
        if (fact)
            priorFacts.push(fact);
    }
    if (latestByContributor.size < 2 && !conflictPattern) {
        return {
            show: false,
            evidence_line: null,
            what_we_know_extra: [],
            silent_winner: false,
            both_retained: true,
        };
    }
    const extras = [];
    const factPriors = [];
    for (const [id, o] of latestByContributor) {
        const role = roleLabel(id);
        const fact = shortFact(o, factPriors);
        if (!fact)
            continue;
        factPriors.push(fact);
        extras.push(role ? `${role}: ${fact}` : fact);
    }
    const capped = extras.slice(0, 3);
    if (capped.length < 2 && !conflictPattern) {
        return {
            show: false,
            evidence_line: null,
            what_we_know_extra: [],
            silent_winner: false,
            both_retained: true,
        };
    }
    return {
        show: capped.length >= 2 || conflictPattern,
        evidence_line: capped.length >= 2
            ? `Different views held: ${capped.join(" · ")} — both kept.`
            : "More than one view is held — nothing was erased.",
        what_we_know_extra: capped,
        silent_winner: false,
        both_retained: true,
    };
}
