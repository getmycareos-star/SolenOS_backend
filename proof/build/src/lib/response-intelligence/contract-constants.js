"use strict";
/** Response Intelligence contract constants. */
Object.defineProperty(exports, "__esModule", { value: true });
exports.RESPONSE_HARD_FAILURE_CHECKS = exports.RESPONSE_GOLDEN_SOFT_INPUTS = exports.RESPONSE_AI_PRODUCT_LANGUAGE_BANS = exports.RESPONSE_OUTPUT_FIELDS = exports.RESPONSE_INTELLIGENCE_PIPELINE = exports.RESPONSE_INTELLIGENCE_PURPOSE = void 0;
exports.RESPONSE_INTELLIGENCE_PURPOSE = "Transform unpredictable caregiver input into trustworthy care understanding — meaning over language patterns.";
exports.RESPONSE_INTELLIGENCE_PIPELINE = [
    "meaning_understanding",
    "compare_with_living_care_record",
    "identify_changes_relationships_unknowns",
    "generate_care_understanding_output",
];
/** Structured outcome fields — generated from understanding, not a fill-in form. */
exports.RESPONSE_OUTPUT_FIELDS = [
    "what_is_happening",
    "what_matters_now",
    "what_to_ask_next",
    "risk_level",
    "what_can_wait",
    "follow_up_items",
];
/** Caregiver-visible AI product / mechanics language — never show. */
exports.RESPONSE_AI_PRODUCT_LANGUAGE_BANS = [
    "i analyzed",
    "i extracted",
    "i detected",
    "based on my analysis",
    "according to the uploaded document",
    "care event created",
    "entity identified",
    "confidence score",
    "classification",
    "sentiment detected",
    "sentiment analysis",
    "extraction complete",
    "ocr completed",
    "ocr confidence",
    "parsing complete",
    "as an ai",
    "ai thinks",
    "i recommend",
    "it appears diagnosed",
];
/**
 * Soft / vague caregiver inputs that must still produce useful orientation.
 * Illustrations only — never phrase-specific product rules.
 */
exports.RESPONSE_GOLDEN_SOFT_INPUTS = [
    "Things have been strange lately.",
    "She wasn't herself today.",
    "The hospital changed something but I don't remember why.",
    "I found this discharge paper.",
    "I don't know what I am supposed to do.",
];
exports.RESPONSE_HARD_FAILURE_CHECKS = [
    "chatbot_conversation",
    "medical_advice",
    "diagnosis_claims",
    "generic_empathy",
    "document_summary_without_meaning",
    "unnecessary_questions",
    "fake_certainty",
    "isolated_event_restart",
    "continuity_loss",
    "ai_product_language",
];
