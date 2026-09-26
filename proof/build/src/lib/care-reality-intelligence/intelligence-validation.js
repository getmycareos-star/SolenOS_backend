"use strict";
/**
 * Hard Rejection & Intelligence Validation Layer.
 * Rejects outputs that look smart but do not improve care-reality understanding.
 *
 * SoT: docs/02-product/solenos-intelligence-validation.md
 * Doc examples are illustrations only — never product if-branches on scenario nouns.
 */
Object.defineProperty(exports, "__esModule", { value: true });
exports.INTELLIGENCE_GATE_QUESTION = exports.HARD_REJECTION_FAMILY_DISTRACTION_PATTERNS = exports.HARD_REJECTION_GENERIC_SAFETY_PATTERNS = exports.HARD_REJECTION_TASK_PATTERNS = exports.INTELLIGENCE_VALIDATION_PURPOSE = void 0;
exports.isSentenceSummaryFailure = isSentenceSummaryFailure;
exports.isTaskGeneratorFailure = isTaskGeneratorFailure;
exports.isGenericSafetyFailure = isGenericSafetyFailure;
exports.isFamilyDistractionFailure = isFamilyDistractionFailure;
exports.isExcessiveQuestioningFailure = isExcessiveQuestioningFailure;
exports.buildIntelligenceChecklist = buildIntelligenceChecklist;
exports.validateIntelligenceResponse = validateIntelligenceResponse;
exports.assertIntelligenceValidation = assertIntelligenceValidation;
const care_recipient_anchor_1 = require("./care-recipient-anchor");
const situation_generator_1 = require("./situation-generator");
const care_reality_memory_1 = require("./care-reality-memory");
const uncertainty_preservation_1 = require("./uncertainty-preservation");
exports.INTELLIGENCE_VALIDATION_PURPOSE = "Reject responses that summarize, task-ify, or distract — require orientation that improves understanding of changing care reality.";
/** Failure 2 — task / monitor theater. */
exports.HARD_REJECTION_TASK_PATTERNS = [
    /\bhere are your tasks\b/i,
    /\bthings to do\b/i,
    /\bto-?do list\b/i,
    /\baction items?:/i,
    /\b☐|\b\[\s*\]/u,
    /\bmonitor (?:symptoms|eating|sleep|confusion)\b/i,
    /\bcall (?:the )?doctor\b/i,
    /\bcheck medication\b/i,
    /\bwatch eating\b/i,
    /\btrack sleep\b/i,
    /\bcare checklist\b/i,
];
/** Failure 3 — generic safety without care-reality context. */
exports.HARD_REJECTION_GENERIC_SAFETY_PATTERNS = [
    /\bconfusion and falls can be serious\b/i,
    /\bplease contact (?:a |your )?healthcare provider\b/i,
    /\bseek (?:immediate )?medical attention\b/i,
    /\bthis (?:can be|may be) (?:a )?medical emergency\b/i,
    /\bconsult (?:your )?doctor (?:immediately|right away)\b/i,
];
/** Failure 4 — family disagreement centered. */
exports.HARD_REJECTION_FAMILY_DISTRACTION_PATTERNS = [
    /\byour brother may (?:need to|not) understand\b/i,
    /\byour sister (?:may|needs to) understand\b/i,
    /\bfamily (?:needs to|should) (?:understand|agree)\b/i,
    /\bthe (?:main|biggest) (?:issue|problem) is (?:your )?(?:brother|sister|family)\b/i,
];
/** Understanding signals — orientation language, not bare “medication change”. */
const UNDERSTANDING_SIGNALS = /\b(?:appear(?:s)? (?:to have )?changed|what changed|has changed|usual (?:pattern|)|previous (?:pattern|routine|)|baseline|connect(?:ed|ion|s)?|may (?:be )?related|around the same|still unclear|remains unclear|reason remains unclear|what remains unclear|possible(?: connection| relationship| factors)?|care reality|current understanding|what we (?:know|understand)|several (?:care )?concerns|care concerns|held from what you shared|organized so they stay connected|what was (?:usual|normal) before|Held so far)\b/i;
function blobFromComposed(composed) {
    return [
        composed.recognition_line ?? "",
        composed.confirmation,
        composed.situation_summary ?? "",
        ...(composed.what_we_know ?? []),
        composed.connection_note ?? "",
        composed.what_changed ?? "",
        composed.what_matters_now ?? "",
        ...(composed.still_unclear ?? []),
        composed.care_story_update ?? "",
    ].join("\n");
}
/** Structural care facets from input — not clinical keyword product banks. */
function extractInputFacets(text) {
    const t = text.toLowerCase();
    const facets = [];
    const cues = [
        { id: "confused", re: /\bconfused|confusion\b/ },
        { id: "leave_home", re: /\bleav(?:e|ing) (?:the )?(?:house|home)|tried leaving\b/ },
        { id: "eating", re: /\beating|eat(?:s|ing)?|appetite\b/ },
        { id: "sleep", re: /\bsleep(?:ing)?|tired\b/ },
        { id: "fall", re: /\bfall|fell|fall scare\b/ },
        { id: "medication", re: /\bmedication|medicine\b/ },
        { id: "hospital", re: /\bhospital|discharg\b/ },
    ];
    for (const c of cues) {
        if (c.re.test(t))
            facets.push(c.id);
    }
    return facets;
}
/**
 * Failure 1: response mostly restates input facets without understanding language.
 */
function isSentenceSummaryFailure(params) {
    const facets = extractInputFacets(params.latestRawText);
    if (facets.length < 3)
        return false;
    const blob = params.responseBlob.toLowerCase();
    const facetHits = facets.filter((f) => {
        if (f === "leave_home")
            return /leav|house|home/i.test(blob);
        if (f === "fall")
            return /fall|fell/i.test(blob);
        return blob.includes(f.replace("_", " ")) || new RegExp(f, "i").test(blob);
    }).length;
    const restatesMost = facetHits >= Math.min(4, facets.length);
    const hasUnderstanding = UNDERSTANDING_SIGNALS.test(params.responseBlob);
    // Comma-list echo of the whole message (classic summary failure)
    const listEcho = /(?:confused|confusion).{0,80}(?:leav|house).{0,80}(?:eat|sleep).{0,80}(?:fall|medication)/i.test(params.responseBlob) && !hasUnderstanding;
    return (restatesMost && !hasUnderstanding) || listEcho;
}
function isTaskGeneratorFailure(blob) {
    return exports.HARD_REJECTION_TASK_PATTERNS.some((p) => p.test(blob));
}
function isGenericSafetyFailure(params) {
    if (!exports.HARD_REJECTION_GENERIC_SAFETY_PATTERNS.some((p) => p.test(params.responseBlob))) {
        return false;
    }
    // Generic safety without timing / baseline / uncertainty / connection → fail
    const hasContext = UNDERSTANDING_SIGNALS.test(params.responseBlob) ||
        /\b(?:usual|previous|since|after|around|unclear|unknown)\b/i.test(params.responseBlob);
    return !hasContext;
}
function isFamilyDistractionFailure(params) {
    if (exports.HARD_REJECTION_FAMILY_DISTRACTION_PATTERNS.some((p) => p.test(params.responseBlob))) {
        return true;
    }
    return (0, care_recipient_anchor_1.centersContributorConflictOverRecipient)({
        blob: params.responseBlob,
        careRecipient: params.careRecipient,
        hasRecipientChanges: params.hasRecipientChanges,
    });
}
function isExcessiveQuestioningFailure(params) {
    if (params.stillUnclear.length > 3)
        return true;
    // Interview battery in a single blob
    const qMarks = (params.responseBlob.match(/\?/g) ?? []).length;
    if (qMarks >= 4)
        return true;
    const interviewCues = /\bhow old\b.+\bwhat medication\b.+\bwhat dosage\b|\bwhen did this start\b.+\bhow often\b.+\bwhat dosage\b/i;
    return interviewCues.test(params.responseBlob);
}
/**
 * Midnight caregiver checklist — critical items must pass for multi-facet care captures.
 */
function buildIntelligenceChecklist(params) {
    return [
        {
            id: "who",
            label: "Identified who this care story is about",
            passed: params.careRecipientIdentified,
            critical: true,
        },
        {
            id: "baseline",
            label: "Compared against baseline or used initial assessment",
            passed: params.hasBaselineOrInitialAssessment,
            critical: true,
        },
        {
            id: "changed",
            label: "Identified what changed",
            passed: params.identifiedChange,
            critical: true,
        },
        {
            id: "connected",
            label: "Connected related events (possible, not proven)",
            passed: params.connectedRelatedEvents,
            critical: false,
        },
        {
            id: "uncertainty",
            label: "Preserved uncertainty",
            passed: params.preservedUncertainty,
            critical: true,
        },
        {
            id: "cause",
            label: "Avoided pretending to know the cause",
            passed: params.avoidedInventedCause,
            critical: true,
        },
        {
            id: "clarity",
            label: "Reduced confusion",
            passed: params.reducedConfusion,
            critical: true,
        },
        {
            id: "work",
            label: "Avoided creating unnecessary work",
            passed: params.avoidedUnnecessaryWork,
            critical: true,
        },
        {
            id: "midnight",
            label: "Would help a caregiver at midnight",
            passed: params.helpsAtMidnight,
            critical: true,
        },
    ];
}
/**
 * Run hard rejection + checklist. Returns ok=false when caregiver must not see this output.
 */
function validateIntelligenceResponse(params) {
    const blob = blobFromComposed(params.composed);
    const failures = [];
    if (isSentenceSummaryFailure({ latestRawText: params.latestRawText, responseBlob: blob })) {
        failures.push("sentence_summary");
    }
    if (isTaskGeneratorFailure(blob)) {
        failures.push("task_generator");
    }
    if (isGenericSafetyFailure({
        responseBlob: blob,
        latestRawText: params.latestRawText,
    })) {
        failures.push("generic_safety");
    }
    if (isFamilyDistractionFailure({
        responseBlob: blob,
        careRecipient: params.careRecipient,
        hasRecipientChanges: params.hasRecipientChanges ?? true,
    })) {
        failures.push("family_distraction");
    }
    if (isExcessiveQuestioningFailure({
        stillUnclear: params.composed.still_unclear ?? [],
        responseBlob: blob,
    })) {
        failures.push("excessive_questioning");
    }
    if ((0, situation_generator_1.containsSituationSummaryTheater)(blob) || (0, care_reality_memory_1.containsTextMemoryTheater)(blob)) {
        if (!failures.includes("sentence_summary"))
            failures.push("sentence_summary");
    }
    if ((0, uncertainty_preservation_1.containsCausalTheater)(blob)) {
        failures.push("causal_theater");
    }
    const rich = params.isRichCareCapture ?? extractInputFacets(params.latestRawText).length >= 3;
    const hasUnderstanding = UNDERSTANDING_SIGNALS.test(blob);
    const hasUnknown = (params.composed.still_unclear?.length ?? 0) > 0 ||
        /\b(?:unclear|not (?:yet )?held|unknown|whether)\b/i.test(blob);
    const hasChange = Boolean(params.composed.what_changed?.trim()) ||
        /\b(?:changed|change|different|usual|previous)\b/i.test(blob);
    const hasConnection = Boolean(params.composed.connection_note?.trim()) ||
        /\b(?:related|connect|around the same|may (?:be )?related)\b/i.test(blob);
    const inventedCause = (0, uncertainty_preservation_1.containsCausalTheater)(blob) ||
        /\b(?:caused by|because of the dementia|medication caused)\b/i.test(blob);
    const checklist = buildIntelligenceChecklist({
        // Named identity preferred; pronoun-centered recipient changes also count (Locked A —
        // display name may not be set yet while the story is clearly about the person).
        careRecipientIdentified: Boolean(params.careRecipient && params.careRecipient !== "they") ||
            Boolean(params.hasRecipientChanges),
        hasBaselineOrInitialAssessment: Boolean(params.hasComparablePrior) ||
            Boolean(params.isInitialAssessment) ||
            !rich,
        identifiedChange: !rich || hasChange || hasUnderstanding,
        connectedRelatedEvents: !rich || hasConnection || hasUnderstanding,
        preservedUncertainty: !rich || hasUnknown || (params.composed.still_unclear?.length ?? 0) <= 3,
        avoidedInventedCause: !inventedCause,
        reducedConfusion: hasUnderstanding || !rich,
        avoidedUnnecessaryWork: !isTaskGeneratorFailure(blob),
        helpsAtMidnight: hasUnderstanding &&
            !isTaskGeneratorFailure(blob) &&
            !isExcessiveQuestioningFailure({
                stillUnclear: params.composed.still_unclear ?? [],
                responseBlob: blob,
            }),
    });
    const criticalFail = checklist.some((c) => c.critical && !c.passed);
    if (criticalFail && rich) {
        failures.push("checklist_incomplete");
    }
    const ok = failures.length === 0;
    let reason = null;
    if (!ok) {
        reason = `Intelligence validation failed: ${failures.join(", ")}`;
    }
    return { ok, failures, checklist, reason };
}
/**
 * Hard gate — throw so failed output never reaches the caregiver.
 */
function assertIntelligenceValidation(params) {
    const result = validateIntelligenceResponse(params);
    if (!result.ok) {
        throw new Error(result.reason ?? "Intelligence validation: response rejected — does not improve care-reality understanding");
    }
}
/** Gate question — documentation / tests. */
exports.INTELLIGENCE_GATE_QUESTION = "Does this response help the caregiver understand the changing care reality better?";
