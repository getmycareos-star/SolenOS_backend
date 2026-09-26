"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.MULTILINGUAL_RESPONSE_HEADER = exports.PRESERVED_DOMAIN_TERMS = exports.SOLENOS_LANGUAGE_NAMES = exports.DEFAULT_SOLENOS_LANGUAGE = exports.SOLENOS_LANGUAGES = void 0;
exports.SOLENOS_LANGUAGES = [
    "en",
    "es",
    "zh",
    "tl",
    "vi",
    "ko",
    "fa",
    "ar",
    "ru",
    "hy",
];
exports.DEFAULT_SOLENOS_LANGUAGE = "en";
exports.SOLENOS_LANGUAGE_NAMES = {
    en: "English",
    es: "Spanish",
    zh: "Chinese (Simplified)",
    tl: "Tagalog",
    vi: "Vietnamese",
    ko: "Korean",
    fa: "Persian / Farsi",
    ar: "Arabic",
    ru: "Russian",
    hy: "Armenian",
};
/** Domain terms that must remain in original form across output languages. */
exports.PRESERVED_DOMAIN_TERMS = [
    "Medi-Cal",
    "Medicare",
    "hospital",
    "doctor",
    "insurance",
];
exports.MULTILINGUAL_RESPONSE_HEADER = "x-solenos-language";
