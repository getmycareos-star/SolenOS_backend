"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.buildEvolvingUnderstandingLines = buildEvolvingUnderstandingLines;
exports.stageFromSignals = stageFromSignals;
exports.evolveSynthesis = evolveSynthesis;
exports.evolveUnderstandingDelta = evolveUnderstandingDelta;
const care_epistemics_1 = require("../care-epistemics");
const care_reality_extraction_1 = require("../care-reality-extraction");
const output_quality_1 = require("../output-quality");
const detect_signals_1 = require("./detect-signals");
const clarity_pillars_1 = require("./clarity-pillars");
/** Build evolving understanding lines — latest state first. */
function buildEvolvingUnderstandingLines(situation) {
    const obs = situation.observations;
    if (obs.length === 0)
        return [];
    const lines = [];
    const seen = new Set();
    const push = (raw, prefix) => {
        const fact = raw.trim();
        if (!fact || !(0, clarity_pillars_1.isCaregiverFacingFactLine)(fact))
            return;
        const normalized = fact.endsWith(".") ? fact : `${fact}.`;
        const key = normalized.toLowerCase().replace(/^earlier:\s*/i, "");
        if (seen.has(key))
            return;
        seen.add(key);
        lines.push(prefix ? `${prefix}${normalized}` : normalized);
    };
    const latest = obs[obs.length - 1];
    const latestFact = (0, care_epistemics_1.observationCareFact)({
        human_fact: latest.human_fact,
        raw_text: latest.raw_text,
    }) ??
        ((0, clarity_pillars_1.isCaregiverFacingFactLine)(latest.human_fact || latest.raw_text)
            ? (latest.human_fact || latest.raw_text).trim()
            : null);
    if (latestFact)
        push(latestFact);
    const priorFacts = latestFact ? [latestFact] : [];
    for (const o of [...obs.slice(0, -1)].reverse()) {
        if (lines.length >= 3)
            break;
        const fact = (0, care_epistemics_1.observationCareFact)({
            human_fact: o.human_fact,
            raw_text: o.raw_text,
            priorFacts,
        }) ??
            ((0, clarity_pillars_1.isCaregiverFacingFactLine)(o.human_fact || o.raw_text)
                ? (o.human_fact || o.raw_text).trim()
                : null);
        if (fact) {
            push(fact, "Earlier: ");
            priorFacts.push(fact);
        }
    }
    return lines;
}
function stageFromSignals(observationCount, signals) {
    const emotional = (0, detect_signals_1.emotionalSignalCount)(signals);
    const pattern = (0, detect_signals_1.patternLabelFor)(signals);
    if (observationCount >= 3 || (pattern && observationCount >= 2 && emotional >= 2)) {
        return "synthesizing";
    }
    if (observationCount >= 2 || emotional >= 2)
        return "forming";
    return "gathering";
}
function clipUnderstandingSpan(text, max = 110) {
    return text.replace(/\s+/g, " ").trim().replace(/\.$/, "").slice(0, max);
}
/**
 * Plain Living Care Record summary — never AI analysis voice, never notes-app chrome.
 */
function evolveSynthesis(params) {
    const { situation, stage, signals, latestSignals } = params;
    void signals;
    if (stage === "gathering" && !(0, detect_signals_1.isImprovementUpdate)(latestSignals))
        return null;
    if ((0, detect_signals_1.isImprovementUpdate)(latestSignals)) {
        const latest = situation.observations[situation.observations.length - 1]?.human_fact?.trim() ||
            situation.observations[situation.observations.length - 1]?.raw_text?.trim();
        if (latest) {
            return `${latest.replace(/\.$/, "")} — held as the latest picture.`;
        }
        return "The latest update changes what we understand. Earlier understanding stays in the care story.";
    }
    if (stage === "gathering")
        return null;
    // Orient from held facts — never phrase if-branches (frustrated+sad+go home scripts).
    const held = situation.observations
        .map((o) => o.human_fact.trim())
        .filter(Boolean)
        .slice(-2);
    if (held.length >= 2) {
        return `${held.join(" ")} Held as one care situation.`;
    }
    if (held.length === 1) {
        return `${held[0].replace(/\.$/, "")} — held with the care situation underway.`;
    }
    return null;
}
/**
 * Understanding delta from extraction — new observation / related event / after-visit outcome.
 * Never notes-app chrome (“related note”, “today’s notes”).
 */
function evolveUnderstandingDelta(params) {
    const { prior, signals, priorSignals, latestSignals = [], latestRawText, } = params;
    if (!prior || prior.observations.length === 0) {
        return null;
    }
    if ((0, detect_signals_1.isImprovementUpdate)(latestSignals)) {
        return "The latest update changes what we understand. Earlier understanding stays in the care story.";
    }
    const latestText = latestRawText?.trim() ||
        // Fall back to newest observation on the situation when caller omits raw text.
        "";
    if (latestText) {
        const extraction = (0, care_reality_extraction_1.extractCareRealityFromText)({ rawText: latestText });
        const outcome = extraction.outcomes[0];
        if (outcome?.description) {
            const desc = clipUnderstandingSpan(outcome.description);
            if ((0, output_quality_1.isNearRawCaregiverFacet)(desc, latestText)) {
                return "After what was already underway — held as what followed.";
            }
            return `After what was already underway: ${desc} — held as what followed.`;
        }
        const event = extraction.events[0];
        if (event?.description) {
            const desc = clipUnderstandingSpan(event.description);
            if ((0, output_quality_1.isNearRawCaregiverFacet)(desc, latestText)) {
                return "A related care update is held with the situation underway.";
            }
            return `A related care update is held with the situation underway: ${desc}.`;
        }
        const observation = extraction.observations[0];
        if (observation?.description) {
            const desc = clipUnderstandingSpan(observation.description);
            if ((0, output_quality_1.isNearRawCaregiverFacet)(desc, latestText)) {
                return "A new observation is held with the care situation underway.";
            }
            return `A new observation is held with the care situation underway: ${desc}.`;
        }
        if (extraction.decisions[0]?.description) {
            const desc = clipUnderstandingSpan(extraction.decisions[0].description);
            if ((0, output_quality_1.isNearRawCaregiverFacet)(desc, latestText)) {
                return "A care choice is held with the situation underway.";
            }
            return `A care choice is held with the situation underway: ${desc}.`;
        }
    }
    const newSignals = signals.filter((s) => !priorSignals.includes(s) && s !== "general");
    if (newSignals.length > 0) {
        return "A new observation is held with the care situation underway.";
    }
    return "Understanding of the care situation was updated.";
}
