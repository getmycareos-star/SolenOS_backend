"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.rewriteMedicalBoundaryOutput = rewriteMedicalBoundaryOutput;
const constants_1 = require("./constants");
function rewriteText(text) {
    let next = text;
    for (const pattern of constants_1.DIAGNOSIS_PATTERNS) {
        next = next.replace(pattern, constants_1.SAFE_UNCERTAINTY_PHRASE);
    }
    for (const pattern of constants_1.DIAGNOSTIC_CERTAINTY_PATTERNS) {
        next = next.replace(pattern, constants_1.SAFE_UNCERTAINTY_PHRASE);
    }
    for (const pattern of constants_1.TREATMENT_PATTERNS) {
        next = next.replace(pattern, constants_1.SAFE_CONSULTATION_PHRASE);
    }
    for (const pattern of constants_1.MEDICATION_INSTRUCTION_PATTERNS) {
        next = next.replace(pattern, constants_1.SAFE_CONSULTATION_PHRASE);
    }
    for (const pattern of constants_1.CLINICAL_AUTHORITY_PATTERNS) {
        next = next.replace(pattern, "Follow existing clinical guidance and ask the care team about any conflicts.");
    }
    return next.replace(/\s{2,}/g, " ").trim();
}
function rewriteMedicalBoundaryOutput(output) {
    return {
        ...output,
        what_is_happening: rewriteText(output.what_is_happening),
        what_matters_now: rewriteText(output.what_matters_now),
        what_to_ask_next: rewriteText(output.what_to_ask_next),
        what_can_wait: rewriteText(output.what_can_wait),
    };
}
