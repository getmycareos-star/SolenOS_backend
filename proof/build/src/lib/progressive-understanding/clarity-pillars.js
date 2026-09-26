"use strict";
/**
 * Caregiver clarity pillars — match solenosai.netlify.app product promise.
 * Surfaces only: What matters now · What can wait · What may become serious.
 * Orient from held care facts + baseline change — never fall/eat/med phrase templates.
 * SoT: docs/02-product/solenos-final-intelligence-refinement.md · solenos-output-quality.md
 */
Object.defineProperty(exports, "__esModule", { value: true });
exports.heldFocusLines = heldFocusLines;
exports.buildCareClarityPillars = buildCareClarityPillars;
exports.buildGuidanceOrientationPillars = buildGuidanceOrientationPillars;
exports.stripCaregiverGuidancePhrases = stripCaregiverGuidancePhrases;
exports.isCaregiverGuidanceDemand = isCaregiverGuidanceDemand;
exports.isCaregiverFacingFactLine = isCaregiverFacingFactLine;
const detect_signals_1 = require("./detect-signals");
const care_epistemics_1 = require("../care-epistemics");
const care_reality_output_1 = require("../care-reality-output");
const care_reality_extraction_1 = require("../care-reality-extraction");
const output_quality_1 = require("../output-quality");
const no_hardcode_contract_1 = require("../care-reality-intelligence/no-hardcode-contract");
function nameOf(situation) {
    if (situation.subject_label === "Mom" || situation.subject_label === "Dad") {
        return situation.subject_label;
    }
    return "they";
}
/** Short caregiver-facing focus lines from held observations — Observation layer only.
 * Prefer partitioned extraction (observation/event/action) over human_fact ≈ raw.
 * Latest first; never fall back to an older raw note as "what stands out".
 * Locked A: session kinship display only — never durable Mom/Dad write from notes.
 */
function heldFocusLines(situation, max = 2) {
    const lines = [];
    const observations = [...situation.observations];
    if (observations.length === 0)
        return [];
    // Latest first; only walk older observations when collecting multiple focus lines.
    const ordered = [...observations].reverse();
    for (const o of ordered) {
        const isLatest = o === observations[observations.length - 1];
        const sourceText = (o.raw_text || o.human_fact || "").trim();
        // Prefer extraction-partitioned surfaces over human_fact ≈ raw.
        const fromExtraction = (0, care_reality_extraction_1.caregiverFacingLinesFromCaptureText)({
            rawText: sourceText,
            max: 1,
            includeEvents: true,
            includeActions: true,
        });
        if (fromExtraction[0]) {
            const line = (0, care_reality_extraction_1.applySessionKinshipDisplay)(fromExtraction[0], situation.subject_label);
            if (!lines.some((l) => l.toLowerCase() === line.toLowerCase())) {
                lines.push(line);
            }
            if (lines.length >= max)
                break;
            continue;
        }
        const careFact = (0, care_epistemics_1.observationCareFact)({
            human_fact: o.human_fact,
            raw_text: o.raw_text,
        });
        // When human_fact is a near-raw dump of a long capture, still partition raw into clauses —
        // do not skip the observation entirely (messy multi-concern notes).
        const partitionSource = careFact && !(0, output_quality_1.isNearRawCaregiverFacet)(careFact, o.raw_text)
            ? careFact
            : (o.raw_text || careFact || "").trim();
        if (!partitionSource || partitionSource.length < 4) {
            if (isLatest && max === 1)
                break;
            continue;
        }
        const raw = partitionSource.replace(/\s+/g, " ");
        let cleaned = (0, care_reality_extraction_1.applySessionKinshipDisplay)(raw
            .replace(/^(hi|hello|hey)[,.]?\s*/i, "")
            .replace(/\bi'?m\s+\w+[,.]?\s*/i, "")
            .replace(/\.{2,}/g, ". ")
            .replace(/\bherefusedto\b/gi, "refused to")
            .replace(/\s+/g, " ")
            .trim(), situation.subject_label);
        if (!cleaned) {
            if (isLatest && max === 1)
                break;
            continue;
        }
        // Prefer Observation-layer clauses — never load, disagreement, events, or decisions as standout.
        // Partition compound / comma-joined notes so we never treat the whole capture as one focus.
        const rawLen = (o.raw_text || "").replace(/\s+/g, " ").trim().length;
        const clauses = cleaned
            .split(/[.!?]+|—|–/)
            .flatMap((c) => (0, care_reality_extraction_1.splitCompoundCareClauses)(c.trim()))
            .map((c) => c.trim())
            .filter((c) => c.length >= 4 && c.length <= 100)
            .filter((c) => {
            const cat = (0, care_reality_extraction_1.classifyExtractionFragment)(c);
            // Partitioned observation/event clauses are expected subsets of long captures —
            // do not reject them as "near raw" (that gate is for quoting whole notes elsewhere).
            if ((cat === "observation" || cat === "event") &&
                rawLen >= 80 &&
                c.length < Math.floor(rawLen * 0.55)) {
                return true;
            }
            return !(0, output_quality_1.looksLikeRawNoteDump)(c, o.raw_text) && !(0, output_quality_1.isNearRawCaregiverFacet)(c, o.raw_text);
        })
            .filter((c) => !(0, care_reality_output_1.looksLikeCaregiverExperienceOnly)(c) &&
            !(0, care_reality_output_1.looksLikeDisagreementPerspectiveLine)(c) &&
            !(0, care_reality_extraction_1.looksLikeCareJourneyEventFragment)(c) &&
            !(0, care_reality_extraction_1.looksLikeCareDecisionFragment)(c) &&
            (0, care_reality_extraction_1.classifyExtractionFragment)(c) !== "contributor_load" &&
            (0, care_reality_extraction_1.classifyExtractionFragment)(c) !== "disagreement_perspective");
        // Rank by care-reality attention (condition → safety/event → decision → …) — never keyword banks.
        const ranked = [...clauses].sort((a, b) => {
            const aRank = (0, no_hardcode_contract_1.attentionRankForExtractionCategory)((0, care_reality_extraction_1.classifyExtractionFragment)(a));
            const bRank = (0, no_hardcode_contract_1.attentionRankForExtractionCategory)((0, care_reality_extraction_1.classifyExtractionFragment)(b));
            if (aRank !== bRank)
                return aRank - bRank;
            // Prefer mid-length (concrete) over very long compound clauses
            const aScore = Math.abs(a.length - 70);
            const bScore = Math.abs(b.length - 70);
            return aScore - bScore;
        });
        const clause = ranked[0] ?? null;
        if (!clause || clause.length < 4) {
            // Latest turn had no usable focus — do not steal an older observation as standout.
            if (isLatest && max === 1)
                break;
            continue;
        }
        const line = clause.endsWith(".") ? clause : `${clause}.`;
        if (lines.some((l) => l.toLowerCase() === line.toLowerCase()))
            continue;
        if ((0, care_reality_output_1.looksLikeCaregiverExperienceOnly)(line) ||
            (0, care_reality_output_1.looksLikeDisagreementPerspectiveLine)(line)) {
            if (isLatest && max === 1)
                break;
            continue;
        }
        lines.push(line);
        if (lines.length >= max)
            break;
        // After taking latest, only continue if we still need more focus lines.
    }
    return lines.reverse();
}
function orientFromHeldFacts(situation, baselineChange = null) {
    const who = nameOf(situation);
    const facts = heldFocusLines(situation, 2);
    if (facts.length === 0 && !baselineChange)
        return null;
    const focus = facts.length === 0
        ? null
        : facts.length === 1
            ? facts[0].replace(/\.$/, "")
            : facts.map((f) => f.replace(/\.$/, "")).join("; ");
    const whoFocus = !focus
        ? null
        : who === "they"
            ? focus
            : focus.replace(/^(Dad|Mom|They|She|He)\b/i, who);
    const repeated = situation.observations.length >= 3;
    const latestRaw = situation.observations[situation.observations.length - 1]?.raw_text ?? null;
    return {
        what_matters_now: (0, output_quality_1.buildMattersNowOrientation)({
            subjectLabel: who === "they" ? null : who,
            heldFocus: whoFocus,
            baselineChange,
            topUnknown: null,
            patternContinues: repeated,
            latestRawText: latestRaw,
        }),
        what_can_wait: "Explaining every detail or deciding everything for the week tonight.",
        what_may_become_serious: (0, care_reality_output_1.buildMayBecomeSeriousLine)({
            subjectLabel: who === "they" ? null : who,
            hasRepeatedPattern: repeated,
            hasEscalationSignal: false,
        }),
    };
}
function buildCareClarityPillars(params) {
    const { situation, stage, signals, latestSignals, patternLabel } = params;
    void params.kind;
    void latestSignals;
    const who = nameOf(situation);
    const baselineChange = params.baselineChangeNote?.trim() || null;
    const latestText = params.latestRawText ??
        situation.observations[situation.observations.length - 1]?.raw_text ??
        "";
    if ((0, detect_signals_1.isImprovementUpdate)(latestSignals)) {
        const fromHeld = orientFromHeldFacts(situation, baselineChange);
        if (fromHeld) {
            return {
                ...fromHeld,
                what_matters_now: `${fromHeld.what_matters_now.replace(/\.$/, "")} — notice if it holds.`,
                what_may_become_serious: null,
            };
        }
        return {
            what_matters_now: "The latest update changes what we understand — notice whether it continues.",
            what_can_wait: "Replaying earlier hard moments tonight.",
            what_may_become_serious: null,
        };
    }
    // Early gather: still orient from held facts when present — never empty “Stay with…” theater.
    if (stage === "gathering" || situation.observations.length <= 1) {
        const fromHeld = orientFromHeldFacts(situation, baselineChange);
        if (fromHeld && ((0, care_epistemics_1.hasOrientableCareContent)(latestText) || baselineChange)) {
            return {
                ...fromHeld,
                what_may_become_serious: null,
            };
        }
        return {
            what_matters_now: (0, output_quality_1.buildMattersNowOrientation)({
                subjectLabel: who === "they" ? null : who,
                heldFocus: null,
                baselineChange,
                topUnknown: null,
            }),
            what_can_wait: "Explaining every detail or answering questions you do not know yet.",
            what_may_become_serious: null,
        };
    }
    const fromHeld = orientFromHeldFacts(situation, baselineChange);
    if (fromHeld && ((0, care_epistemics_1.hasOrientableCareContent)(latestText) || baselineChange)) {
        const escalate = (0, detect_signals_1.emotionalSignalCount)(signals) >= 2 ||
            Boolean(patternLabel && /gradual|pattern|fluctuation|transition/i.test(patternLabel));
        return {
            ...fromHeld,
            what_may_become_serious: (0, care_reality_output_1.buildMayBecomeSeriousLine)({
                subjectLabel: who === "they" ? null : who,
                hasRepeatedPattern: situation.observations.length >= 3,
                hasEscalationSignal: escalate,
            }),
        };
    }
    if (patternLabel || stage === "synthesizing") {
        return {
            what_matters_now: (0, output_quality_1.buildMattersNowOrientation)({
                subjectLabel: who === "they" ? null : who,
                heldFocus: null,
                baselineChange: baselineChange ||
                    (who === "they"
                        ? "What is held today may differ from what was usual"
                        : `What is held today may differ from ${who}'s usual`),
                topUnknown: null,
            }),
            what_can_wait: "Explaining every moment tonight.",
            what_may_become_serious: (0, care_reality_output_1.buildMayBecomeSeriousLine)({
                subjectLabel: who === "they" ? null : who,
                hasRepeatedPattern: situation.observations.length >= 3,
                hasEscalationSignal: (0, detect_signals_1.emotionalSignalCount)(signals) >= 2,
            }),
        };
    }
    return {
        what_matters_now: (0, output_quality_1.buildMattersNowOrientation)({
            subjectLabel: who === "they" ? null : who,
            heldFocus: null,
            baselineChange,
            topUnknown: null,
        }),
        what_can_wait: "Explaining every detail or answering questions you do not know yet.",
        what_may_become_serious: null,
    };
}
/**
 * When the caregiver asks "what should I do?", orient from held care reality —
 * never invent medical advice, never echo the question as a fact.
 */
function buildGuidanceOrientationPillars(params) {
    const { situation, signals } = params;
    const who = nameOf(situation);
    const them = who === "they" ? "them" : who;
    const possessive = who === "they" ? "their" : who === "Mom" ? "her" : "his";
    const baselineChange = params.baselineChangeNote?.trim() || null;
    const fromHeld = orientFromHeldFacts(situation, baselineChange);
    if (fromHeld)
        return fromHeld;
    if ((0, detect_signals_1.emotionalSignalCount)(signals) >= 1) {
        return {
            what_matters_now: `Right now: how ${them} seem compared with ${possessive} usual day.`,
            what_can_wait: "Finding a perfect label for every feeling tonight.",
            what_may_become_serious: (0, care_reality_output_1.buildMayBecomeSeriousLine)({
                subjectLabel: who === "they" ? null : who,
                hasRepeatedPattern: false,
                hasEscalationSignal: (0, detect_signals_1.emotionalSignalCount)(signals) >= 2,
            }),
        };
    }
    return {
        what_matters_now: (0, output_quality_1.buildMattersNowOrientation)({
            subjectLabel: who === "they" ? null : who,
            heldFocus: null,
            baselineChange,
            topUnknown: null,
        }),
        what_can_wait: "Deciding everything for the week tonight.",
        what_may_become_serious: null,
    };
}
/** Guidance / Continuity Demand phrases — strip before holding care facts. */
const GUIDANCE_PHRASE_RE = /\bwhat should i (?:do|say|tell|ask)\b|\bwhat do i do\b|\bwhat'?s next\b|\bwhat should (?:we|i) (?:do|happen)\b|\bwhat now\b/gi;
/**
 * Remove Continuity Demand asks so mixed notes keep the care remainder.
 * Pure guidance collapses to empty.
 */
function stripCaregiverGuidancePhrases(text) {
    return text
        .replace(GUIDANCE_PHRASE_RE, " ")
        .replace(/^(?:hi[,.]?\s*)?(?:should i|how do i)\b/i, " ")
        .replace(/[?]+/g, " ")
        .replace(/\s+/g, " ")
        .replace(/^[,.\s:;!-]+|[,.\s:;!-]+$/g, "")
        .trim();
}
function remainderLooksCareShaped(remainder) {
    const r = remainder.trim();
    if (r.length < 20)
        return false;
    if (/\b(mom|dad|she|he|they|her|his|their)\b/i.test(r))
        return true;
    if ((0, care_epistemics_1.looksLikeConcreteObservation)(r))
        return true;
    if ((0, care_epistemics_1.hasOrientableCareContent)(r))
        return true;
    return false;
}
/**
 * Continuity Demand / decision fatigue — pure or dominant only.
 * Mixed care report + “what do I do?” keeps the care report (not Continuity Demand).
 */
function isCaregiverGuidanceDemand(text) {
    const t = text.trim();
    if (!t)
        return false;
    const lower = t.toLowerCase();
    if (/^(hi[,.]?\s*)?(what should i do|what do i do|what'?s next|what now|should i)\??\.?$/i.test(lower)) {
        return true;
    }
    if (/^(your (mom|dad|loved one)):\s*what should/i.test(lower))
        return true;
    const hasGuidance = /\bwhat should i (do|say|tell|ask)\b/i.test(t) ||
        /\bwhat do i do\b/i.test(t) ||
        /\bwhat'?s next\b/i.test(t) ||
        /\bwhat should (we|i) (do|happen)\b/i.test(t) ||
        /^(should i|how do i|what now)\b/i.test(t);
    if (!hasGuidance)
        return false;
    const remainder = stripCaregiverGuidancePhrases(t);
    if (remainderLooksCareShaped(remainder))
        return false;
    return true;
}
function isCaregiverFacingFactLine(line) {
    const t = line.trim();
    if (!t)
        return false;
    if (/\?/.test(t) && /\b(yes|no|today|baseline|time of day)\b/i.test(t))
        return false;
    if (/^(is this|when did|does it|did they|what has|what would|what else|what should)/i.test(t)) {
        return false;
    }
    if (/usual baseline|particular time of day|matters most about this/i.test(t))
        return false;
    // Product/session meta must never appear as a care fact
    if (/\b(hi|hello|hey)\s+solenos\b/i.test(t) ||
        (/\bsolenos\b/i.test(t) &&
            /\b(help me|recommended|first time here)\b/i.test(t) &&
            !/\b(mom|dad|she|he)\b.{0,40}\b(fell|eat|talk|stay|ask)\b/i.test(t))) {
        return false;
    }
    return true;
}
