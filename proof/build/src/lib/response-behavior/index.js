"use strict";
/**
 * Response Behavior layer — facet selection + turn classification.
 * Product SoT: golden caregiver scenarios + Response Contract.
 * Engines propose state; composer speaks; this module decides *what may speak*.
 *
 * Permanent Clarity rule: incomplete understanding → Clarity forbidden for soft-only notes.
 * understandingSufficient unlocks Clarity — including light orientation for orientable care content.
 * Soft vague mood alone never bypasses gather (G1).
 */
Object.defineProperty(exports, "__esModule", { value: true });
exports.careRealityObservations = exports.careContextGapsRemain = exports.earlyGatherIncomplete = exports.understandingSufficient = void 0;
exports.classifyCaregiverTurn = classifyCaregiverTurn;
exports.evidenceMaturityFor = evidenceMaturityFor;
exports.resolveReliefDecisionForTurn = resolveReliefDecisionForTurn;
exports.selectResponseFacets = selectResponseFacets;
exports.composeWhyAsking = composeWhyAsking;
exports.composeEvidenceLine = composeEvidenceLine;
const clarity_pillars_1 = require("../progressive-understanding/clarity-pillars");
const resolve_uncertainty_1 = require("../progressive-understanding/resolve-uncertainty");
const detect_signals_1 = require("../progressive-understanding/detect-signals");
const questions_1 = require("../progressive-understanding/questions");
Object.defineProperty(exports, "earlyGatherIncomplete", { enumerable: true, get: function () { return questions_1.earlyGatherIncomplete; } });
Object.defineProperty(exports, "understandingSufficient", { enumerable: true, get: function () { return questions_1.understandingSufficient; } });
Object.defineProperty(exports, "careContextGapsRemain", { enumerable: true, get: function () { return questions_1.careContextGapsRemain; } });
Object.defineProperty(exports, "careRealityObservations", { enumerable: true, get: function () { return questions_1.careRealityObservations; } });
const care_epistemics_1 = require("../care-epistemics");
const output_quality_1 = require("../output-quality");
const relief_decision_1 = require("../response-contract/relief-decision");
const multi_caregiver_context_model_1 = require("../multi-caregiver-context-model");
const feedback_containment_1 = require("../telemetry-persistence/feedback-containment");
function classifyCaregiverTurn(params) {
    if (params.turn.identity_mismatch)
        return "identity_mismatch";
    const text = params.latestRawText.trim();
    if (!text || /^(update|nothing new|ok|okay|\.+)$/i.test(text)) {
        return "empty_or_thin";
    }
    if ((0, resolve_uncertainty_1.isCaregiverQuestionPushback)(text))
        return "pushback";
    if ((0, clarity_pillars_1.isCaregiverGuidanceDemand)(text))
        return "continuity_symptom";
    if (/\b(why is|why are|why was|why did|why does)\b/i.test(text) &&
        /\b(medication|medicine|pill|dose|taking|this)\b/i.test(text)) {
        return "record_question";
    }
    if (params.hasDocuments && text.length < 8)
        return "document";
    if (params.hasDocuments && /^\[document:/i.test(text))
        return "document";
    const signals = (0, detect_signals_1.detectObservationSignals)(text, params.kind);
    if ((0, detect_signals_1.isImprovementUpdate)(signals))
        return "improvement";
    // Align with isCareRealityAnchorText (+ ACS thread context) — Slice 5.4.
    // Care-reality anchors (incl. soft mood / NL unease / thin thread continuations)
    // are never emotional_only; pure caregiver-load without care content still is.
    const sequentialPriors = [];
    for (const o of params.turn.situation.observations.slice(0, -1)) {
        const fact = (0, care_epistemics_1.observationCareFact)({
            human_fact: o.human_fact,
            raw_text: o.raw_text,
            priorFacts: sequentialPriors,
        });
        if (fact)
            sequentialPriors.push(fact);
    }
    const isCareAnchor = (0, care_epistemics_1.isCareRealityAnchorText)(text, { priorFacts: sequentialPriors });
    const emotionalOnly = !isCareAnchor &&
        /\b(i'?m exhausted|i am exhausted|i'?m overwhelmed|i don'?t know what to do|i cant keep up|i can't keep up)\b/i.test(text) &&
        !/\b(mom|dad|she|he|fell|eat|medication|doctor)\b/i.test(text);
    if (emotionalOnly)
        return "emotional_only";
    if (params.turn.relation === "answers_uncertainty" ||
        (params.turn.resolved_uncertainties.length > 0 &&
            params.turn.relation !== "opens_new")) {
        return "answer_to_open";
    }
    return "observation";
}
function evidenceMaturityFor(params) {
    const careN = (0, questions_1.careRealityObservations)(params.turn.situation).length;
    const n = careN > 0
        ? careN
        : params.turn.crs_observation_count || params.turn.situation.observations.length;
    const revision = params.turn.crs_revision || Math.max(1, n);
    const pattern = Boolean(params.turn.pattern_label);
    const established = params.turn.disclosure_stage === "established";
    if (params.turnClass === "record_question")
        return 5;
    // First document capture is still Level 1 — source + what recorded only.
    if (params.hasDocuments && revision <= 1 && n <= 1)
        return 1;
    if (params.hasDocuments && revision >= 5)
        return 5;
    if (params.hasDocuments && (n >= 2 || revision >= 2))
        return 2;
    if (established && (revision >= 8 || n >= 4))
        return 10;
    if (n <= 1 && revision <= 1)
        return 1;
    if (n === 2 || revision === 2)
        return 2;
    if (pattern || n >= 3 || revision >= 3)
        return 3;
    return 2;
}
/** Locked relief decision for a turn — shared by composer, ingest, and LCR merge. */
function resolveReliefDecisionForTurn(params) {
    const latest = params.latestRawText.trim();
    const softVague = (0, care_epistemics_1.isSoftVagueMoodNote)(latest);
    const sufficient = (0, questions_1.understandingSufficient)({
        situation: params.turn.situation,
    });
    const gapsRemain = (0, questions_1.careContextGapsRemain)({ situation: params.turn.situation });
    const careN = (0, questions_1.careRealityObservations)(params.turn.situation).length;
    const latestIsCareWorthy = (0, questions_1.latestObservationIsCareWorthy)(params.turn.situation);
    const base = (0, relief_decision_1.decideReliefDisclosure)({
        turnClass: params.turnClass,
        softVague,
        understandingSufficient: sufficient,
        careContextGapsRemain: gapsRemain,
        careWorthyCount: careN,
        latestIsCareWorthy,
        latestRawText: latest,
    });
    const careKey = (0, multi_caregiver_context_model_1.resolveCareRealityStoreKey)(params.turn.situation.care_recipient_id ?? params.turn.situation.caregiver_id);
    const containment = (0, feedback_containment_1.peekFeedbackContainmentAdaptation)(careKey);
    return (0, feedback_containment_1.applyFeedbackContainmentToRelief)(base, containment);
}
/**
 * Adaptive facets — driven by locked relief decision tree.
 * Soft-only notes stay gather-first (G1). Orientable care unlocks Response Contract relief.
 */
function selectResponseFacets(params) {
    const maturity = evidenceMaturityFor({
        turn: params.turn,
        turnClass: params.turnClass,
        hasDocuments: params.hasDocuments,
    });
    const { turnClass, gatheringContext } = params;
    const latest = params.latestRawText ??
        params.turn.situation.observations[params.turn.situation.observations.length - 1]
            ?.raw_text ??
        "";
    const softVague = (0, care_epistemics_1.isSoftVagueMoodNote)(latest);
    const relief = resolveReliefDecisionForTurn({
        turn: params.turn,
        turnClass,
        latestRawText: latest,
    });
    const sufficient = (0, questions_1.understandingSufficient)({
        situation: params.turn.situation,
    });
    const careN = (0, questions_1.careRealityObservations)(params.turn.situation).length;
    const gatherIncomplete = !sufficient;
    const showClarity = relief.show_clarity;
    const step1 = careN <= 1;
    const askCap = relief.max_asks > 0 ? relief.max_asks : step1 ? 1 : softVague ? 1 : 3;
    if (turnClass === "empty_or_thin") {
        return {
            show_confirmation: true,
            show_what_we_know: false,
            show_what_is_happening: relief.show_what_is_happening,
            show_what_changed: false,
            show_clarity: relief.show_clarity,
            show_asks: relief.show_asks,
            show_why_asking: false,
            show_evidence_line: false,
            show_follow_up: relief.show_follow_up,
            max_asks: relief.max_asks,
            evidence_maturity: 1,
        };
    }
    if (turnClass === "identity_mismatch") {
        return {
            show_confirmation: true,
            show_what_we_know: true,
            show_what_is_happening: false,
            show_what_changed: false,
            show_clarity: false,
            show_asks: true,
            show_why_asking: false,
            show_evidence_line: false,
            show_follow_up: false,
            max_asks: 1,
            evidence_maturity: 1,
        };
    }
    if (turnClass === "record_question") {
        return {
            show_confirmation: true,
            show_what_we_know: true,
            show_what_is_happening: false,
            show_what_changed: false,
            show_clarity: false,
            show_asks: false,
            show_why_asking: false,
            show_evidence_line: true,
            show_follow_up: false,
            max_asks: 0,
            evidence_maturity: 5,
        };
    }
    if (turnClass === "answer_to_open") {
        return {
            show_confirmation: true,
            show_what_we_know: true,
            show_what_is_happening: relief.show_what_is_happening,
            show_what_changed: true,
            show_clarity: relief.show_clarity,
            show_asks: params.turn.resolved_uncertainties.length > 0 ? false : relief.show_asks,
            show_why_asking: false,
            show_evidence_line: true,
            show_follow_up: relief.show_follow_up,
            max_asks: params.turn.resolved_uncertainties.length > 0 ? 0 : relief.max_asks,
            evidence_maturity: maturity,
        };
    }
    // Pushback: never re-ask — Clarity only if understanding already sufficient
    if (turnClass === "pushback") {
        return {
            show_confirmation: true,
            show_what_we_know: true,
            show_what_is_happening: relief.show_what_is_happening,
            show_what_changed: false,
            show_clarity: relief.show_clarity,
            show_asks: false,
            show_why_asking: false,
            show_evidence_line: false,
            show_follow_up: false,
            max_asks: 0,
            evidence_maturity: maturity,
        };
    }
    // Incomplete understanding: Held + facts + gap asks — Clarity forbidden (soft/thin).
    if (gatherIncomplete || gatheringContext) {
        const priorHeld = params.turn.situation.observations.length > 0;
        const obsCount = careN > 0 ? careN : params.turn.situation.observations.length;
        const gatherMaturity = Math.min(maturity, 2);
        return {
            show_confirmation: true,
            show_what_we_know: turnClass !== "emotional_only" || priorHeld,
            show_what_is_happening: relief.show_what_is_happening,
            show_what_changed: false,
            show_clarity: relief.show_clarity,
            show_asks: relief.show_asks,
            show_why_asking: obsCount <= 1 && turnClass !== "emotional_only",
            show_evidence_line: true,
            show_follow_up: relief.show_follow_up,
            max_asks: turnClass === "emotional_only" ? 1 : askCap,
            evidence_maturity: gatherMaturity,
        };
    }
    // Sufficient — Response Contract relief (locked decision tree)
    if (turnClass === "emotional_only") {
        return {
            show_confirmation: true,
            show_what_we_know: true,
            show_what_is_happening: relief.show_what_is_happening,
            show_what_changed: true,
            show_clarity: showClarity,
            show_asks: relief.show_asks,
            show_why_asking: false,
            show_evidence_line: true,
            show_follow_up: relief.show_follow_up,
            max_asks: relief.max_asks,
            evidence_maturity: Math.max(maturity, 2),
        };
    }
    if (turnClass === "document") {
        return {
            show_confirmation: true,
            show_what_we_know: true,
            show_what_is_happening: maturity >= 2 || relief.show_what_is_happening,
            show_what_changed: false,
            show_clarity: showClarity,
            show_asks: relief.show_asks,
            show_why_asking: false,
            show_evidence_line: true,
            show_follow_up: relief.show_follow_up,
            max_asks: relief.max_asks,
            evidence_maturity: maturity,
        };
    }
    if (turnClass === "improvement") {
        return {
            show_confirmation: true,
            show_what_we_know: true,
            show_what_is_happening: true,
            show_what_changed: true,
            show_clarity: showClarity,
            show_asks: false,
            show_why_asking: false,
            show_evidence_line: maturity >= 2,
            show_follow_up: false,
            max_asks: 0,
            evidence_maturity: maturity,
        };
    }
    if (turnClass === "continuity_symptom") {
        return {
            show_confirmation: true,
            show_what_we_know: true,
            show_what_is_happening: true,
            show_what_changed: true,
            show_clarity: showClarity,
            show_asks: false,
            show_why_asking: false,
            show_evidence_line: true,
            show_follow_up: true,
            max_asks: 0,
            evidence_maturity: Math.max(maturity, 2),
        };
    }
    return {
        show_confirmation: true,
        show_what_we_know: true,
        show_what_is_happening: relief.show_what_is_happening,
        show_what_changed: relief.show_clarity,
        show_clarity: showClarity,
        show_asks: relief.show_asks,
        show_why_asking: false,
        show_evidence_line: true,
        show_follow_up: relief.show_follow_up,
        max_asks: relief.max_asks,
        evidence_maturity: maturity,
    };
}
/** Quiet L1 why-asking — never engine jargon. */
function composeWhyAsking(params) {
    void params.signals;
    const who = params.subjectLabel === "Mom" || params.subjectLabel === "Dad"
        ? params.subjectLabel
        : "them";
    return `These questions help place what you shared in ${who === "them" ? "their" : `${who}'s`} care reality.`;
}
/**
 * Evidence line by maturity ladder (Evidence Visibility Directive).
 * Never confidence %, graphs, or engine jargon.
 * Maturity 2+: only short structured facets — never join near-raw note blobs.
 */
function composeEvidenceLine(params) {
    const source = params.hasDocuments
        ? "Source: your document"
        : "Source: what you shared · Today";
    const structuredFacts = (params.supportingFacts ?? [])
        .map((f) => f.trim())
        .filter((f) => Boolean(f) &&
        f.length <= 48 &&
        !(0, output_quality_1.isNearRawCaregiverFacet)(f, params.latestRawText))
        .slice(0, 3);
    const unknowns = (params.openUncertainties ?? [])
        .map((u) => u.trim())
        .filter(Boolean)
        .slice(0, 2);
    const revisions = (params.revisionSummaries ?? [])
        .map((r) => r.trim())
        .filter((r) => Boolean(r) &&
        r.length <= 72 &&
        !(0, output_quality_1.isNearRawCaregiverFacet)(r, params.latestRawText))
        .slice(0, 3);
    if (params.maturity <= 1) {
        return source;
    }
    if (params.maturity === 2) {
        // Drop raw join entirely when no structured facets — source alone is enough.
        if (structuredFacts.length === 0)
            return source;
        return `${source}. Related: ${structuredFacts.slice(0, 2).join(" · ")}`;
    }
    if (params.maturity === 3) {
        const parts = [source];
        if (structuredFacts.length > 0) {
            parts.push(`Supporting: ${structuredFacts.join(" · ")}`);
        }
        if (unknowns.length > 0)
            parts.push(`Still unclear: ${unknowns.join(" · ")}`);
        return parts.join(". ");
    }
    if (params.maturity === 5) {
        const parts = [source];
        if (structuredFacts.length > 0) {
            parts.push(`Found: ${structuredFacts.join(" · ")}`);
        }
        if (unknowns.length > 0)
            parts.push(`Open: ${unknowns[0]}`);
        return parts.join(". ");
    }
    // Level 10 — major change: evolution + uncertainty
    const evolution = revisions.length > 0
        ? revisions.join(" → ")
        : structuredFacts.length > 0
            ? structuredFacts.join(" → ")
            : null;
    if (!evolution)
        return source;
    const uncertainty = unknowns.length > 0 ? ` Uncertainty: ${unknowns.join(" · ")}` : "";
    return `How we got here: ${evolution}.${uncertainty}`;
}
