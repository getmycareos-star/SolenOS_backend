"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.classifyInputSurface = classifyInputSurface;
const contract_constants_1 = require("./contract-constants");
const signals_1 = require("./signals");
function countMatches(text, patterns) {
    return patterns.filter((pattern) => pattern.test(text)).length;
}
function scoreMode(text, patterns) {
    const matches = countMatches(text, patterns);
    if (matches === 0)
        return 0;
    if (matches >= 2)
        return 0.92;
    return 0.72;
}
/**
 * Surface-signal classifier — shallow, conservative, non-interpretive routing only.
 * Does NOT understand meaning. Selects behavioral constraints for downstream generation.
 */
function classifyInputSurface(input) {
    const text = input.trim();
    if (!text) {
        return { mode: contract_constants_1.LOW_CONFIDENCE_DEFAULT_MODE, confidence: 0 };
    }
    const scores = {
        crisis_urgent: scoreMode(text, signals_1.CRISIS_URGENT_SIGNALS),
        medical_document: scoreMode(text, signals_1.MEDICAL_DOCUMENT_SIGNALS),
        administrative_legal: scoreMode(text, signals_1.ADMINISTRATIVE_LEGAL_SIGNALS),
        emotional_narrative: scoreMode(text, signals_1.EMOTIONAL_NARRATIVE_SIGNALS),
    };
    const priority = [
        "crisis_urgent",
        "medical_document",
        "administrative_legal",
        "emotional_narrative",
    ];
    let bestMode = contract_constants_1.LOW_CONFIDENCE_DEFAULT_MODE;
    let bestScore = 0;
    for (const mode of priority) {
        if (scores[mode] > bestScore) {
            bestScore = scores[mode];
            bestMode = mode;
        }
    }
    if (bestScore < contract_constants_1.LOW_CONFIDENCE_THRESHOLD) {
        return { mode: contract_constants_1.LOW_CONFIDENCE_DEFAULT_MODE, confidence: bestScore };
    }
    return { mode: bestMode, confidence: bestScore };
}
