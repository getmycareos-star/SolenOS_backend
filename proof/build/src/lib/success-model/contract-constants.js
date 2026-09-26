"use strict";
/** Success model — outcome metrics over activity metrics. */
Object.defineProperty(exports, "__esModule", { value: true });
exports.MIN_FEATURE_ACCEPTANCE_YES = exports.FEATURE_ACCEPTANCE_QUESTIONS = exports.LONGITUDINAL_METRICS = exports.USER_TRUST_METRICS = exports.SYSTEM_QUALITY_METRICS = exports.PRIMARY_SUCCESS_METRICS = exports.ACTIVITY_METRICS = exports.SUCCESS_MODEL_IDENTITY = void 0;
exports.SUCCESS_MODEL_IDENTITY = "SolenOS succeeds when caregivers no longer carry the entire care journey in their heads.";
exports.ACTIVITY_METRICS = [
    "ai_conversations",
    "documents_uploaded",
    "events_stored",
    "graph_size",
    "time_in_app",
];
exports.PRIMARY_SUCCESS_METRICS = [
    "cognitive_load_reduction",
    "continuity_restoration",
    "meeting_preparation_efficiency",
    "follow_up_reliability",
    "recall_accuracy",
];
exports.SYSTEM_QUALITY_METRICS = [
    "extraction_confidence",
    "unresolved_uncertainty_count",
    "user_corrections",
    "duplicate_event_rate",
    "event_linking_accuracy",
    "document_processing_accuracy",
    "follow_up_completion_rate",
];
exports.USER_TRUST_METRICS = [
    "corrections_accepted",
    "confidence_in_extraction",
    "provenance_coverage",
    "evidence_supported_answers",
    "fabricated_events",
];
exports.LONGITUDINAL_METRICS = [
    "connected_events",
    "resolved_uncertainties",
    "linked_relationships",
    "reusable_historical_context",
    "repeated_entry_reduction",
];
exports.FEATURE_ACCEPTANCE_QUESTIONS = [
    "Does it reduce mental and cognitive overload?",
    "Does it preserve or restore continuity?",
    "Does it reduce reliance on memory?",
    "Does it improve understanding of what has changed?",
    "Does it help prevent missed context or follow-ups?",
    "Is its value measurable using success metrics?",
];
exports.MIN_FEATURE_ACCEPTANCE_YES = 4;
