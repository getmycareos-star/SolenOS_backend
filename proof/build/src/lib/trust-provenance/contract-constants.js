"use strict";
/** Trust & provenance — trust is visible system behavior, not a product promise. */
Object.defineProperty(exports, "__esModule", { value: true });
exports.GENERATION_FORBIDDEN = exports.GENERATION_ALLOWED = exports.RETRIEVAL_PIPELINE_STEPS = exports.EVIDENCE_KINDS = exports.PROVENANCE_SOURCE_TYPES = exports.TRUST_INDICATOR_KINDS = exports.RESPONSE_CONFIDENCE_LEVELS = exports.INSUFFICIENT_EVIDENCE_MESSAGE = exports.TRUST_PROVENANCE_IDENTITY = void 0;
exports.TRUST_PROVENANCE_IDENTITY = "SolenOS does not generate truth. It reconstructs continuity from evidence.";
exports.INSUFFICIENT_EVIDENCE_MESSAGE = "I don't have enough information to answer this confidently.";
exports.RESPONSE_CONFIDENCE_LEVELS = [
    "high",
    "medium",
    "low",
    "insufficient",
];
exports.TRUST_INDICATOR_KINDS = [
    "verified_by_caregiver",
    "extracted_from_document",
    "confirmed_follow_up",
    "awaiting_confirmation",
    "low_confidence",
    "missing_evidence",
];
exports.PROVENANCE_SOURCE_TYPES = [
    "user_input",
    "voice",
    "text",
    "document",
    "ocr_text",
    "pdf",
    "image",
    "correction",
];
exports.EVIDENCE_KINDS = [
    "care_event",
    "document",
    "correction",
    "timeline_ref",
    "unresolved_uncertainty",
];
exports.RETRIEVAL_PIPELINE_STEPS = [
    "retrieve_care_events",
    "retrieve_linked_documents",
    "retrieve_user_corrections",
    "retrieve_unresolved_uncertainties",
    "generate_from_retrieved_context_only",
];
exports.GENERATION_ALLOWED = [
    "organize_information",
    "reconstruct_timelines",
    "explain_relationships",
    "identify_missing_information",
    "highlight_inconsistencies",
    "suggest_clarification_questions",
];
exports.GENERATION_FORBIDDEN = [
    "invent_events",
    "invent_dates",
    "invent_relationships",
    "assume_intentions",
    "predict_outcomes_without_evidence",
    "present_speculation_as_fact",
];
