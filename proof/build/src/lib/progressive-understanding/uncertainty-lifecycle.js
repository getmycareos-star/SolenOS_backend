"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.gapFamilySatisfiedInCareBlob = gapFamilySatisfiedInCareBlob;
exports.filterOpenUncertaintiesForCareBlob = filterOpenUncertaintiesForCareBlob;
exports.reconcileOpenUncertainties = reconcileOpenUncertainties;
exports.filterSessionUncertaintyAsks = filterSessionUncertaintyAsks;
exports.projectOpenUncertaintiesForState = projectOpenUncertaintiesForState;
const care_epistemics_1 = require("../care-epistemics");
const resolve_uncertainty_1 = require("./resolve-uncertainty");
const questions_1 = require("./questions");
function dedupeQuestions(questions) {
    const out = [];
    for (const q of questions) {
        const t = q.trim();
        if (!t)
            continue;
        if (out.some((x) => x.toLowerCase() === t.toLowerCase()))
            continue;
        out.push(t);
    }
    return out;
}
function careBlob(situation) {
    return (0, questions_1.careRealityObservations)(situation)
        .map((o) => (0, care_epistemics_1.observationCareFact)({ human_fact: o.human_fact, raw_text: o.raw_text }) ?? "")
        .filter(Boolean)
        .join("\n");
}
/** True when care observations already hold evidence for a gap family (timing, baseline). */
function gapFamilySatisfiedInCareBlob(situation, family) {
    const blob = careBlob(situation);
    if (family === "timing") {
        return /\b(when|started|began|yesterday|this morning|last night|today|hour|minute|since|ago|week|month)\b/i.test(blob);
    }
    if (family === "baseline") {
        return /\b(usual|normally|always|new for|first time|compared with|not like|again|different from|typical)\b/i.test(blob);
    }
    return false;
}
/** Drop open asks whose gap family is already satisfied in held care observations. */
function filterOpenUncertaintiesForCareBlob(situation, openQuestions) {
    return openQuestions.filter((q) => {
        const family = (0, questions_1.questionFamily)(q);
        if (family === "timing" || family === "baseline") {
            return !gapFamilySatisfiedInCareBlob(situation, family);
        }
        return true;
    });
}
/**
 * Close gaps answered by latest note; merge with prior resolved list for CRS.
 */
function reconcileOpenUncertainties(params) {
    const closedByBlob = [];
    const filtered = params.openQuestions.filter((q) => {
        const family = (0, questions_1.questionFamily)(q);
        if (family === "timing" || family === "baseline") {
            if (gapFamilySatisfiedInCareBlob(params.situation, family)) {
                closedByBlob.push(q);
                return false;
            }
        }
        return true;
    });
    const { remaining, resolved } = (0, resolve_uncertainty_1.resolveAnsweredUncertainties)({
        openQuestions: filtered,
        rawText: params.rawText,
    });
    const allResolved = dedupeQuestions([
        ...(params.priorResolved ?? []),
        ...closedByBlob,
        ...resolved,
    ]);
    const resolvedLower = new Set(allResolved.map((r) => r.toLowerCase()));
    const open = remaining.filter((q) => !resolvedLower.has(q.toLowerCase()));
    return { open, resolved: allResolved };
}
/**
 * Session compose filter — never re-ask the same gap in the same interaction session.
 */
function filterSessionUncertaintyAsks(params) {
    const currentTurnLower = new Set((params.currentTurnAsks ?? params.asks).map((q) => q.toLowerCase()));
    const priorAskedFamilies = new Set(params.askedQuestions
        .filter((q) => !currentTurnLower.has(q.toLowerCase()))
        .map((q) => (0, questions_1.questionFamily)(q)));
    const resolvedFamilies = new Set(params.resolvedUncertainties.map((q) => (0, questions_1.questionFamily)(q)));
    const priorAskedExact = new Set(params.askedQuestions
        .filter((q) => !currentTurnLower.has(q.toLowerCase()))
        .map((q) => q.toLowerCase()));
    return filterOpenUncertaintiesForCareBlob(params.situation, params.asks).filter((q) => {
        const ql = q.toLowerCase();
        if (priorAskedExact.has(ql))
            return false;
        const family = (0, questions_1.questionFamily)(q);
        if (resolvedFamilies.has(family))
            return false;
        if (priorAskedFamilies.has(family))
            return false;
        return true;
    });
}
/** CRS + ACS open list with blob + answer reconciliation applied. */
function projectOpenUncertaintiesForState(params) {
    const merged = dedupeQuestions([
        ...params.crsOpen,
        ...params.situation.open_questions,
    ]).slice(0, 8);
    return reconcileOpenUncertainties({
        situation: params.situation,
        openQuestions: merged,
        rawText: params.rawText,
        priorResolved: params.priorResolved,
    });
}
