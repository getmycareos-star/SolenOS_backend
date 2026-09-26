"use strict";
/** Failure modes & resilience — graceful degradation preserves trust. */
Object.defineProperty(exports, "__esModule", { value: true });
exports.RETRY_BACKOFF_MS = exports.MAX_RETRY_ATTEMPTS = exports.MAX_CLARIFICATION_QUESTIONS = exports.RELATIONSHIP_STATUSES = exports.VERIFICATION_STATUSES = exports.PROCESSING_STATUSES = exports.FAILURE_CATEGORIES = exports.FAILURE_OUTCOMES = exports.FAILURE_RESILIENCE_IDENTITY = void 0;
exports.FAILURE_RESILIENCE_IDENTITY = "Failure is not the opposite of continuity. Losing information is.";
exports.FAILURE_OUTCOMES = ["clarify", "preserve_raw", "defer"];
exports.FAILURE_CATEGORIES = [
    "extraction_failure",
    "incomplete_context",
    "ambiguous_interpretation",
    "graph_linking_failure",
    "conflicting_information",
    "processing_failure",
];
exports.PROCESSING_STATUSES = [
    "complete",
    "partial",
    "pending",
    "deferred",
    "failed_recoverable",
];
exports.VERIFICATION_STATUSES = [
    "unverified",
    "needs_confirmation",
    "user_confirmed",
    "rejected",
];
exports.RELATIONSHIP_STATUSES = [
    "resolved",
    "unresolved",
    "independent",
    "deferred",
];
exports.MAX_CLARIFICATION_QUESTIONS = 3;
exports.MAX_RETRY_ATTEMPTS = 3;
exports.RETRY_BACKOFF_MS = 5000;
