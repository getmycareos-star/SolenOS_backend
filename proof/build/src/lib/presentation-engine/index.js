"use strict";
/**
 * Presentation Engine — PURE projection over a single shared CareContext.
 * Never mutates facts, events, unknowns, confidence, or timeline order.
 */
Object.defineProperty(exports, "__esModule", { value: true });
exports.PRESENTATION_MODES = void 0;
exports.projectPresentation = projectPresentation;
exports.PRESENTATION_MODES = ["essential", "standard", "detailed"];
/**
 * Deterministic, reversible, non-destructive renderer.
 */
function projectPresentation(truth, preference = { mode: "standard" }) {
    const mode = preference.mode;
    const highUnknowns = truth.explicit_unknowns.filter((u) => u.priority === "critical" || u.priority === "high");
    if (mode === "essential") {
        return {
            mode,
            sections: {
                what_changed: truth.what_changed.slice(0, 2),
                what_matters_now: truth.what_needs_attention.slice(0, 2),
                what_is_unknown: highUnknowns.slice(0, 2).map((u) => u.missing_information),
                next_considerations: truth.what_needs_attention.slice(0, 1),
                reasoning_summary: [],
            },
            invariants: {
                single_care_context: true,
                presentation_only: true,
                does_not_mutate_truth: true,
            },
        };
    }
    if (mode === "detailed") {
        return {
            mode,
            sections: {
                what_changed: truth.what_changed,
                what_matters_now: [
                    ...truth.what_is_happening.slice(0, 4),
                    ...truth.what_needs_attention.slice(0, 4),
                ],
                what_is_unknown: truth.explicit_unknowns.map((u) => `${u.missing_information} (${u.priority}): ${u.reason_it_matters}`),
                next_considerations: truth.what_needs_attention,
                reasoning_summary: [
                    ...truth.inferred,
                    ...truth.confidence_notes,
                    ...truth.evidence_summaries,
                ],
                full_detail: truth,
            },
            invariants: {
                single_care_context: true,
                presentation_only: true,
                does_not_mutate_truth: true,
            },
        };
    }
    // standard (default)
    return {
        mode: "standard",
        sections: {
            what_changed: truth.what_changed.slice(0, 4),
            what_matters_now: truth.what_is_happening.slice(0, 3),
            what_is_unknown: highUnknowns.slice(0, 3).map((u) => u.missing_information),
            next_considerations: truth.what_needs_attention.slice(0, 3),
            reasoning_summary: truth.inferred.slice(0, 2),
        },
        invariants: {
            single_care_context: true,
            presentation_only: true,
            does_not_mutate_truth: true,
        },
    };
}
