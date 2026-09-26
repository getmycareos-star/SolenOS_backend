"use strict";
/** Timeline Reconstruction Engine — fragmented memory → coherent chronological care reality. */
Object.defineProperty(exports, "__esModule", { value: true });
exports.ORDERING_CONFLICT_STRATEGY = exports.TIMELINE_RECONSTRUCTION_RULES = exports.TEMPORAL_SIGNAL_PATTERNS = exports.TIMELINE_RECONSTRUCTION_DEFINING_PRINCIPLE = exports.TIMELINE_RECONSTRUCTION_IDENTITY = void 0;
exports.TIMELINE_RECONSTRUCTION_IDENTITY = "Solenos does not store stories. It reconstructs timelines from fragmented truth.";
exports.TIMELINE_RECONSTRUCTION_DEFINING_PRINCIPLE = "Extract temporal references, normalize ordering, preserve uncertainty — never overwrite existing CareEvents.";
exports.TEMPORAL_SIGNAL_PATTERNS = {
    correction: /\b(actually|correction|wait[,—]?\s|no[,—]?\s)/i,
    before: /\b(before that|prior to|earlier than|two weeks before)\b/i,
    after: /\b(after (?:the )?|following|since (?:the )?)\b/i,
    relative: /\b(yesterday|today|last week|last night|this morning|\d+\s+days?\s+ago)\b/i,
};
exports.TIMELINE_RECONSTRUCTION_RULES = [
    "explicit_time_over_narrative_order",
    "preserve_ordering_hypotheses",
    "never_overwrite_care_events",
    "uncertainty_explicit",
    "minimal_clarification_when_clinical",
];
exports.ORDERING_CONFLICT_STRATEGY = "preserve_both_with_confidence";
