"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.classifyUnderstandingEffect = classifyUnderstandingEffect;
exports.buildProgressiveTurnCopy = buildProgressiveTurnCopy;
exports.connectionNoteForProgress = connectionNoteForProgress;
function classifyUnderstandingEffect(params) {
    if (params.relation === "opens_new")
        return "opens_situation";
    if (params.resolvedCount > 0 || params.relation === "answers_uncertainty") {
        return "answers_uncertainty";
    }
    if (params.invalidatesPrior)
        return "invalidates_understanding";
    if (params.introducedDimension)
        return "introduces_new_dimension";
    if (params.mattersChanged)
        return "changes_what_matters";
    if (params.newSignalCount > 0 ||
        params.stage !== params.priorStage ||
        params.relation === "updates_active" ||
        params.relation === "adds_context") {
        return "strengthens_pattern";
    }
    return "continues_gathering";
}
/**
 * Quiet record language — not AI analysis voice.
 * Inspiration: solenosai.netlify.app — relief, not chatbot.
 */
function buildProgressiveTurnCopy(params) {
    const { relation, stage, effect, observationCount } = params;
    if (relation === "opens_new" || observationCount <= 1) {
        return {
            confirmation_title: "Held in the Living Care Record",
            confirmation_body: "This is preserved in the Living Care Record.",
            understanding_heading: "What we know so far",
            insufficiency_note: null,
        };
    }
    let confirmation_title = "Care situation updated";
    let confirmation_body = "Connected to what was already held.";
    let understanding_heading = "What we know so far";
    let insufficiency_note = null;
    if (effect === "answers_uncertainty") {
        confirmation_body = "That helped — something unclear is now clearer.";
    }
    else if (effect === "invalidates_understanding") {
        confirmation_title = "Care situation updated";
        confirmation_body = "The latest update changed the current understanding.";
    }
    else if (effect === "introduces_new_dimension") {
        confirmation_body = "A new part of today's understanding was added.";
    }
    else if (stage === "synthesizing" || effect === "strengthens_pattern") {
        confirmation_body = "Updated today's care situation.";
    }
    if (stage === "forming" && effect !== "answers_uncertainty") {
        insufficiency_note = null;
    }
    return {
        confirmation_title,
        confirmation_body,
        understanding_heading,
        insufficiency_note,
    };
}
function connectionNoteForProgress(params) {
    if (params.relation === "opens_new") {
        return null;
    }
    if (params.effect === "answers_uncertainty") {
        return null;
    }
    if (params.count === 2) {
        return "These may be parts of the same day — kept together.";
    }
    if (params.stage === "synthesizing" || params.count >= 3) {
        return "Related updates stay connected in the Living Care Record.";
    }
    return null;
}
