"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.hasTimeframe = hasTimeframe;
exports.hasSuccessCriteria = hasSuccessCriteria;
exports.hasScopeBoundaries = hasScopeBoundaries;
exports.hasSubjectDefinition = hasSubjectDefinition;
exports.hasStakeholderContext = hasStakeholderContext;
exports.hasCareSignals = hasCareSignals;
exports.isGibberish = isGibberish;
exports.isSubstantiveText = isSubstantiveText;
exports.isTrueAmbiguous = isTrueAmbiguous;
exports.isCareDecompressionContext = isCareDecompressionContext;
const signals_1 = require("../input-classification/signals");
const contract_constants_1 = require("./contract-constants");
const TIMEFRAME_PATTERNS = [
    /\btoday\b/i,
    /\byesterday\b/i,
    /\btomorrow\b/i,
    /\bthis (?:week|month|morning|afternoon|evening)\b/i,
    /\blast (?:week|month|night|year|few days)\b/i,
    /\b(?:since|until|before|after|during)\b/i,
    /\b\d+\s+(?:days?|weeks?|months?|hours?)\s+ago\b/i,
    /\b(?:ago|recently|ongoing|recurring|repeatedly)\b/i,
    /\b(?:once|twice|again)\b/i,
    /\bfor\s+(?:days|weeks|months)\b/i,
    /\b(?:january|february|march|april|may|june|july|august|september|october|november|december)\b/i,
    /\b\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?\b/,
];
const SUCCESS_CRITERIA_PATTERNS = [
    /\b(?:need to|want to|hoping to|trying to|goal is|so that)\b/i,
    /\b(?:make sure|ensure|figure out|resolve|understand|decide)\b/i,
    /\b(?:success|outcome|result|help me|what should i)\b/i,
    /\b(?:before i|so i can|in order to)\b/i,
];
const SCOPE_BOUNDARIES_PATTERNS = [
    /\b(?:only|just|not about|except|specifically|regarding|focus on)\b/i,
    /\b(?:limited to|mainly|primarily|about .+ not)\b/i,
    /\b(?:this is about|not related to|separate from)\b/i,
];
const SUBJECT_DEFINITION_PATTERNS = [
    /\b(?:mom|mother|dad|father|parent|husband|wife|spouse|partner)\b/i,
    /\b(?:grandma|grandmother|grandpa|grandfather|grandparent)\b/i,
    /\b(?:patient|care recipient|loved one|relative)\b/i,
    /\b(?:my|our)\s+\w+/i,
    /\b(?:she|he|they)\b/i,
    /\b[A-Z][a-z]{2,}\b/,
];
const STAKEHOLDER_CONTEXT_PATTERNS = [
    /\b(?:doctor|physician|nurse|caregiver|aide|therapist|specialist)\b/i,
    /\b(?:hospital|clinic|facility|nursing home|hospice|pharmacy)\b/i,
    /\b(?:family|sibling|brother|sister|daughter|son)\b/i,
    /\b(?:insurance|payer|social worker|case manager)\b/i,
];
const MEDICATION_SIGNALS = [
    /\b(?:medication|medicine|meds?|pill|dose|dosage|prescription|insulin)\b/i,
    /\b(?:missed|forgot|skipped|took|refill)\b/i,
];
const SYMPTOM_SIGNALS = [
    /\b(?:pain|fever|cough|confused|forget|forgot|fall|fell|bleeding|nausea)\b/i,
    /\b(?:symptom|worsening|decline|weakness|dizzy|shortness of breath)\b/i,
];
const GIBBERISH_PATTERN = /^[^a-zA-Z]*$|(.)\1{4,}|^(?:asdf|qwerty|xxx|test){1,}$/i;
function matchesAny(text, patterns) {
    return patterns.some((pattern) => pattern.test(text));
}
function hasTimeframe(text) {
    return matchesAny(text, TIMEFRAME_PATTERNS);
}
function hasSuccessCriteria(text) {
    return matchesAny(text, SUCCESS_CRITERIA_PATTERNS);
}
function hasScopeBoundaries(text) {
    return matchesAny(text, SCOPE_BOUNDARIES_PATTERNS);
}
function hasSubjectDefinition(text) {
    return matchesAny(text, SUBJECT_DEFINITION_PATTERNS);
}
function hasStakeholderContext(text) {
    return matchesAny(text, STAKEHOLDER_CONTEXT_PATTERNS);
}
function hasCareSignals(text) {
    return (hasSubjectDefinition(text) ||
        matchesAny(text, MEDICATION_SIGNALS) ||
        matchesAny(text, SYMPTOM_SIGNALS) ||
        matchesAny(text, signals_1.EMOTIONAL_NARRATIVE_SIGNALS) ||
        matchesAny(text, signals_1.MEDICAL_DOCUMENT_SIGNALS));
}
function isGibberish(text) {
    const trimmed = text.trim();
    if (!trimmed)
        return true;
    if (GIBBERISH_PATTERN.test(trimmed))
        return true;
    const alphaRatio = (trimmed.match(/[a-zA-Z]/g)?.length ?? 0) / trimmed.length;
    return alphaRatio < 0.3 && trimmed.length < contract_constants_1.MIN_SUBSTANTIVE_INPUT_LENGTH;
}
function isSubstantiveText(text) {
    const trimmed = text.trim();
    return trimmed.length >= contract_constants_1.MIN_SUBSTANTIVE_INPUT_LENGTH && !isGibberish(trimmed);
}
/** True AMBIGUOUS — only cases that warrant hard BLOCK without LLM. */
function isTrueAmbiguous(text) {
    const trimmed = text.trim();
    if (!trimmed)
        return true;
    if (trimmed.length < contract_constants_1.MIN_SUBSTANTIVE_INPUT_LENGTH)
        return true;
    if (isGibberish(trimmed))
        return true;
    if (!hasSubjectDefinition(trimmed) && !hasStakeholderContext(trimmed) && !hasCareSignals(trimmed)) {
        return true;
    }
    return false;
}
/**
 * SolenOS care decompression bypass — caregiver dumps are intentionally messy.
 * Max clarity downgrade is PARTIAL when care context is detected.
 */
function isCareDecompressionContext(text, inputMode) {
    if (hasCareSignals(text))
        return true;
    if (inputMode && inputMode !== "administrative_legal" && isSubstantiveText(text)) {
        return true;
    }
    return false;
}
