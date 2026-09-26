"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.resolveCrsComposeContext = resolveCrsComposeContext;
exports.crsSupportingFacts = crsSupportingFacts;
const care_epistemics_1 = require("../care-epistemics");
const uncertainty_lifecycle_1 = require("../progressive-understanding/uncertainty-lifecycle");
function filterCareUnderstanding(lines) {
    return lines
        .map((l) => l.trim())
        .filter((l) => l.length > 0 &&
        !(0, care_epistemics_1.isProductSessionMetaText)(l) &&
        ((0, care_epistemics_1.isCareRealityAnchorText)(l) || /you described/i.test(l)));
}
/**
 * Resolve compose inputs: CRS first, turn as fallback; latest is delta when returning.
 */
function resolveCrsComposeContext(params) {
    const { crs, turn, latestIsCareWorthy, isNewCareReality } = params;
    const turnUnderstanding = filterCareUnderstanding(turn.current_understanding);
    const fallbackHeld = turnUnderstanding.length > 0 ? turnUnderstanding : turn.current_understanding;
    const fallback = {
        usesCrsAsSource: false,
        heldUnderstanding: fallbackHeld,
        openUncertainties: [
            ...new Set([...turn.situation.open_questions, ...turn.what_needs_context]),
        ],
        whatChangedInUnderstanding: turn.what_changed_in_understanding,
        understandingRevisions: [],
        supportingEvidence: [],
        situationSummary: turn.what_seems_happening,
        crsRevision: turn.crs_revision ?? 0,
    };
    if (!crs)
        return fallback;
    const crsHeld = filterCareUnderstanding(crs.current_understanding);
    const hasCrsDepth = crsHeld.length > 0 &&
        (!isNewCareReality ||
            (crs.revision ?? 0) >= 2 ||
            (crs.observation_count ?? 0) >= 2);
    if (!hasCrsDepth) {
        return {
            ...fallback,
            heldUnderstanding: crsHeld.length > 0 ? crsHeld : fallbackHeld,
            openUncertainties: crs.open_uncertainties.length > 0
                ? crs.open_uncertainties
                : fallback.openUncertainties,
            understandingRevisions: crs.understanding_revisions ?? [],
            supportingEvidence: crs.supporting_evidence ?? [],
            situationSummary: crs.situation_summary ?? fallback.situationSummary,
            crsRevision: crs.revision,
        };
    }
    const heldUnderstanding = crsHeld.length > 0 ? crsHeld : turnUnderstanding;
    const openUncertainties = (0, uncertainty_lifecycle_1.filterOpenUncertaintiesForCareBlob)(turn.situation, [
        ...new Set([...(crs.open_uncertainties ?? []), ...turn.situation.open_questions]),
    ].filter(Boolean)).filter((q) => !(crs.resolved_uncertainties ?? []).some((r) => r.toLowerCase() === q.toLowerCase()));
    const whatChangedInUnderstanding = latestIsCareWorthy && turn.what_changed_in_understanding
        ? turn.what_changed_in_understanding
        : crs.what_changed_in_understanding ?? turn.what_changed_in_understanding;
    const situationSummary = !latestIsCareWorthy && crs.situation_summary
        ? crs.situation_summary
        : turn.what_seems_happening ?? crs.situation_summary;
    return {
        usesCrsAsSource: true,
        heldUnderstanding,
        openUncertainties,
        whatChangedInUnderstanding,
        understandingRevisions: crs.understanding_revisions ?? [],
        supportingEvidence: crs.supporting_evidence ?? [],
        situationSummary,
        crsRevision: crs.revision,
    };
}
/** Care-anchor facts from CRS supporting evidence for evidence line compose. */
function crsSupportingFacts(evidence, max = 3) {
    return evidence
        .map((e) => e.observation.trim())
        .filter((o) => o.length > 0 && (0, care_epistemics_1.isCareRealityAnchorText)(o))
        .slice(0, max);
}
