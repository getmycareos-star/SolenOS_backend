"use strict";
/** Core state correction + data integrity — continuously repairable event graph. */
Object.defineProperty(exports, "__esModule", { value: true });
exports.INTEGRITY_CORRECTION_TYPES = exports.TRUTH_SOURCE_PRIORITY = exports.CONFIDENCE_LEVELS = exports.CARE_EVENT_STATUSES = exports.TRUST_LOOP = exports.INTEGRITY_IDENTITY = void 0;
exports.INTEGRITY_IDENTITY = "A continuously corrected event-sourced reality model with explicit uncertainty and user-authoritative truth resolution.";
exports.TRUST_LOOP = "Input → Extraction → Event → User Correction → Graph Update → Improved Future Extraction";
exports.CARE_EVENT_STATUSES = [
    "committed",
    "provisional",
    "unparsed_raw",
    "invalidated",
    "superseded",
];
exports.CONFIDENCE_LEVELS = ["low", "medium", "high"];
exports.TRUTH_SOURCE_PRIORITY = [
    "user_correction",
    "validated_document",
    "ai_inference",
];
exports.INTEGRITY_CORRECTION_TYPES = [
    "modify",
    "invalidate",
    "split",
    "clarify",
    "supersede",
];
