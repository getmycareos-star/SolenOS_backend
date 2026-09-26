"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.hasClarifyingQuestions = exports.SOLENOS_STRING_FIELDS = void 0;
exports.collectCaregiverText = collectCaregiverText;
exports.hasClarifyingQuestion = hasClarifyingQuestion;
exports.outputImpliesIncompleteContext = outputImpliesIncompleteContext;
/** String caregiver fields — strict 5-field schema. */
exports.SOLENOS_STRING_FIELDS = [
    "what_is_happening",
    "what_matters_now",
    "what_to_ask_next",
    "what_can_wait",
];
function collectCaregiverText(output) {
    return exports.SOLENOS_STRING_FIELDS.map((field) => output[field]).join("\n");
}
function hasClarifyingQuestion(output) {
    const text = output.what_to_ask_next.trim();
    if (!text)
        return false;
    if (text.endsWith("?"))
        return true;
    return /\[ \].+\?/m.test(text);
}
/** @deprecated Use hasClarifyingQuestion */
exports.hasClarifyingQuestions = hasClarifyingQuestion;
function outputImpliesIncompleteContext(output) {
    const combined = `${output.what_is_happening} ${output.what_matters_now} ${output.what_to_ask_next}`;
    return /\b(cannot be determined|unclear|uncertain|missing|not stated|unknown|uncertainty|contradict|inconsistent|conflicting)\b/i.test(combined);
}
