"use strict";
/**

 * Feedback-Driven Learning Loop (FDLL) — properties of inferences in the same runtime.

 * Only explicit caregiver feedback modifies learning weights (no silent drift).

 */
Object.defineProperty(exports, "__esModule", { value: true });
exports.recordInference = recordInference;
exports.getInference = getInference;
exports.applyInferenceFeedback = applyInferenceFeedback;
exports.listPendingInferences = listPendingInferences;
exports.resetInferenceLearningStore = resetInferenceLearningStore;
exports.getLearningHistory = getLearningHistory;
const inferenceStore = new Map();
const feedbackStore = [];
const weightHistory = [];
function recordInference(inference) {
    inferenceStore.set(inference.inference_id, inference);
    return inference;
}
function getInference(id) {
    return inferenceStore.get(id);
}
/**

 * Explicit feedback only — required architectural rule.

 */
function applyInferenceFeedback(feedback) {
    feedbackStore.push(feedback);
    const inference = inferenceStore.get(feedback.inference_id);
    const reliability = Math.max(0.2, Math.min(1, feedback.feedback_source_reliability));
    let update;
    if (feedback.verdict === "correct") {
        update = {
            inference_id: feedback.inference_id,
            confidence_delta: 0.08 * reliability,
            pattern_strength_delta: 0.1 * reliability,
            pathway_unreliable: false,
            reason: `Correct feedback strengthens ${inference?.engine_source ?? "unknown"} pathway`,
        };
    }
    else if (feedback.verdict === "incorrect") {
        update = {
            inference_id: feedback.inference_id,
            confidence_delta: -0.2 * reliability,
            pattern_strength_delta: -0.15 * reliability,
            pathway_unreliable: true,
            reason: `Incorrect feedback marks ${inference?.engine_source ?? "unknown"} pathway unreliable`,
        };
    }
    else {
        update = {
            inference_id: feedback.inference_id,
            confidence_delta: -0.05 * reliability,
            pattern_strength_delta: 0,
            pathway_unreliable: false,
            components: {
                timing: 0,
                event_detection: 0.05 * reliability,
                progression: -0.05 * reliability,
            },
            reason: "Partial feedback refines granularity — not binary learning",
        };
    }
    weightHistory.push(update);
    return update;
}
function listPendingInferences() {
    const feedbackIds = new Set(feedbackStore.map((f) => f.inference_id));
    return [...inferenceStore.values()].filter((i) => !feedbackIds.has(i.inference_id));
}
function resetInferenceLearningStore() {
    inferenceStore.clear();
    feedbackStore.length = 0;
    weightHistory.length = 0;
}
function getLearningHistory() {
    return [...weightHistory];
}
