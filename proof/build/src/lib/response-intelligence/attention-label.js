"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.ATTENTION_LABELS_BY_RISK = void 0;
exports.humanAttentionLabelFor = humanAttentionLabelFor;
exports.shouldDiscloseAttentionLevel = shouldDiscloseAttentionLevel;
exports.containsAttentionScoreTheater = containsAttentionScoreTheater;
/**
 * Caregiver-facing attention language from Response Contract risk_level.
 * Never scores, %, confidence theater, or "risk_level" enum chrome.
 * SoT: docs/02-product/solenos-response-contract.md § Risk level
 */
exports.ATTENTION_LABELS_BY_RISK = {
    low: "Can wait — not the main focus from what is held",
    medium: "Worth attention from what is held",
    high: "Needs attention now from what is held",
};
function humanAttentionLabelFor(risk) {
    return exports.ATTENTION_LABELS_BY_RISK[risk];
}
/** Disclosure: show attention when consequence warrants — never dump Low on every first note. */
function shouldDiscloseAttentionLevel(params) {
    if (params.risk === "high")
        return true;
    if (params.risk === "medium")
        return true;
    // Low: only once understanding has room to orient (growing+)
    return params.disclosureStage !== "early";
}
/** Reject score / percentage theater in caregiver attention copy. */
function containsAttentionScoreTheater(text) {
    return (/\b\d{1,3}\s*%\b/.test(text) ||
        /\bconfidence\s*(score|level|%)\b/i.test(text) ||
        /\brisk_level\b/i.test(text) ||
        /\brisk\s*score\b/i.test(text));
}
