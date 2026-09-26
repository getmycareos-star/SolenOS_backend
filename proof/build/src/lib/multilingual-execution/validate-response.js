"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.validateMultilingualExecution = validateMultilingualExecution;
const constants_1 = require("./constants");
const validate_language_1 = require("./validate-language");
const ENGLISH_STOPWORDS = new Set([
    "the",
    "and",
    "is",
    "are",
    "was",
    "were",
    "this",
    "that",
    "with",
    "for",
    "you",
    "your",
    "what",
    "when",
    "where",
    "how",
    "should",
    "could",
    "would",
    "may",
    "might",
    "not",
    "but",
    "from",
    "into",
    "about",
    "have",
    "has",
    "had",
    "can",
    "will",
    "be",
    "been",
    "being",
    "do",
    "does",
    "did",
    "a",
    "an",
    "to",
    "of",
    "in",
    "on",
    "at",
    "it",
    "they",
    "their",
    "there",
    "as",
    "or",
    "if",
    "by",
    "than",
    "then",
    "so",
    "just",
    "now",
    "also",
    "only",
    "very",
    "more",
    "most",
    "some",
    "any",
    "all",
    "each",
    "other",
    "such",
    "no",
    "yes",
]);
const SCRIPT_CHECKS = {
    zh: (text) => /[\u4e00-\u9fff]/.test(text),
    fa: (text) => /[\u0600-\u06ff]/.test(text),
    ar: (text) => /[\u0600-\u06ff]/.test(text),
    ko: (text) => /[\uac00-\ud7af]/.test(text),
    hy: (text) => /[\u0530-\u058f]/.test(text),
    ru: (text) => /[\u0400-\u04ff]/.test(text),
};
function stripPreservedTerms(text) {
    let stripped = text;
    for (const term of constants_1.PRESERVED_DOMAIN_TERMS) {
        stripped = stripped.replaceAll(term, " ");
    }
    return stripped;
}
function tokenizeWords(text) {
    return text
        .toLowerCase()
        .replace(/[^a-z\u00c0-\u024f\u0400-\u04ff\u0530-\u058f\u0600-\u06ff\u4e00-\u9fff\uac00-\ud7af\s'-]/g, " ")
        .split(/\s+/)
        .filter(Boolean);
}
function looksEnglishOnly(text) {
    const cleaned = stripPreservedTerms(text).trim();
    if (!cleaned)
        return false;
    const words = tokenizeWords(cleaned);
    if (words.length < 4)
        return false;
    const stopwordHits = words.filter((word) => ENGLISH_STOPWORDS.has(word)).length;
    const ratio = stopwordHits / words.length;
    const asciiLettersOnly = /^[\x00-\x7f\s.,!?;:'"()\-–—[\]{}]+$/.test(cleaned) &&
        /[a-z]/i.test(cleaned);
    return asciiLettersOnly && ratio >= 0.22;
}
function hasExpectedScript(text, language) {
    const checker = SCRIPT_CHECKS[language];
    return checker ? checker(text) : true;
}
/**
 * Pre-return validation gate for multilingual execution consistency.
 */
function validateMultilingualExecution(output, meta, uiLanguage) {
    const violations = [];
    if (!(0, validate_language_1.isSolenOSLanguage)(meta.userLanguage)) {
        violations.push("unsupported_language");
    }
    if (!meta.promptWrapped) {
        violations.push("prompt_not_wrapped");
    }
    if (uiLanguage && uiLanguage !== meta.userLanguage) {
        violations.push("ui_backend_language_mismatch");
    }
    const combined = [
        output.what_is_happening,
        output.what_matters_now,
        output.what_to_ask_next,
        output.what_can_wait,
    ].join("\n");
    if (meta.userLanguage !== "en") {
        if (!hasExpectedScript(combined, meta.userLanguage) && looksEnglishOnly(combined)) {
            violations.push("english_only_leakage");
        }
    }
    return {
        ok: violations.length === 0,
        violations,
    };
}
