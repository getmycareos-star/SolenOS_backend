"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.DECISION_FRAMEWORK_QUESTIONS = exports.ARCHITECTURAL_RULES = void 0;
exports.evaluateAgainstDecisionFramework = evaluateAgainstDecisionFramework;
const contract_constants_1 = require("./contract-constants");
Object.defineProperty(exports, "ARCHITECTURAL_RULES", { enumerable: true, get: function () { return contract_constants_1.ARCHITECTURAL_RULES; } });
Object.defineProperty(exports, "DECISION_FRAMEWORK_QUESTIONS", { enumerable: true, get: function () { return contract_constants_1.DECISION_FRAMEWORK_QUESTIONS; } });
function evaluateAgainstDecisionFramework(input) {
    const failed = [];
    const violated = [];
    if (!input.preserves_truth)
        failed.push(contract_constants_1.DECISION_FRAMEWORK_QUESTIONS[0]);
    if (!input.reduces_uncertainty_without_concealing)
        failed.push(contract_constants_1.DECISION_FRAMEWORK_QUESTIONS[1]);
    if (!input.strengthens_continuity)
        failed.push(contract_constants_1.DECISION_FRAMEWORK_QUESTIONS[2]);
    if (!input.explainable)
        failed.push(contract_constants_1.DECISION_FRAMEWORK_QUESTIONS[3]);
    if (!input.confidence_proportional)
        failed.push(contract_constants_1.DECISION_FRAMEWORK_QUESTIONS[4]);
    if (!input.reduces_burden_without_clinical_replacement) {
        failed.push(contract_constants_1.DECISION_FRAMEWORK_QUESTIONS[5]);
    }
    if (input.may_diagnose)
        violated.push("never_diagnose");
    if (input.may_invent_facts)
        violated.push("never_invent_information");
    if (input.may_hide_uncertainty)
        violated.push("never_hide_uncertainty");
    if (input.may_overwrite_history)
        violated.push("never_overwrite_history");
    if (input.optimizes_engagement)
        violated.push("never_optimize_for_engagement");
    const passes = failed.length === 0 && violated.length === 0;
    let recommendation = "build";
    if (!passes) {
        recommendation =
            violated.length > 0 || input.may_diagnose ? "reject" : "redesign";
    }
    return {
        passes,
        framework_questions: contract_constants_1.DECISION_FRAMEWORK_QUESTIONS,
        failed_questions: failed,
        violated_rules: violated,
        recommendation,
    };
}
