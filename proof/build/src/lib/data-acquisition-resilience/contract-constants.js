"use strict";
/** Data Acquisition + Failure Resilience Engine (DARE) */
Object.defineProperty(exports, "__esModule", { value: true });
exports.COMPLETENESS_LEVELS = exports.EXTRACTION_METHODS = exports.CORRECTION_TYPES = exports.AMBIGUITY_FLAGS = exports.CONFIDENCE_SOURCES = exports.AUTO_VALIDATE_CONFIDENCE = exports.OCR_CONFIDENCE_THRESHOLD = exports.DARE_CORE_RULE = exports.DARE_IDENTITY = void 0;
exports.DARE_IDENTITY = "Probabilistic ingestion where every extracted fact is provisional until validated.";
exports.DARE_CORE_RULE = "The graph is NOT built from extraction. The graph is built from resolved truth.";
exports.OCR_CONFIDENCE_THRESHOLD = 0.4;
exports.AUTO_VALIDATE_CONFIDENCE = 0.65;
exports.CONFIDENCE_SOURCES = [
    "ocr",
    "nlp_model",
    "user_confirmation",
    "cross_document_match",
    "repeated_signal",
];
exports.AMBIGUITY_FLAGS = [
    "who_is_he",
    "who_is_they",
    "what_changed",
    "when",
    "unclear_reference",
    "ocr_unreadable",
    "contradictory_sources",
    "partial_signal",
    "voice_corruption",
];
exports.CORRECTION_TYPES = ["modify", "delete", "merge", "clarify"];
exports.EXTRACTION_METHODS = [
    "regex_nlp",
    "ocr",
    "voice_transcript",
    "document_parse",
    "user_input",
];
exports.COMPLETENESS_LEVELS = ["complete", "partial", "insufficient"];
