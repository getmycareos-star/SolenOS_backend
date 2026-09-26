"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.processProductNorthStar = processProductNorthStar;
const contract_constants_1 = require("./contract-constants");
const classify_demand_1 = require("./classify-demand");
const evaluate_feature_1 = require("./evaluate-feature");
function coverImplicitOutputs(input) {
    const happening = input.final_what_is_happening ?? "";
    const matters = input.final_what_matters_now ?? "";
    const canWait = input.final_what_can_wait ?? "";
    const changed = input.what_changed ?? [];
    return {
        what_changed: changed.length > 0 ||
            input.has_meaningful_diff === true ||
            /\b(changed|wors|new|fell|increased|decreased)\b/i.test(happening),
        what_matters_now: matters.trim().length > 0,
        what_should_i_remember: happening.trim().length > 0 || input.has_care_events === true,
        what_can_i_ignore: canWait.trim().length > 0,
    };
}
function processProductNorthStar(input) {
    const demand = input.raw_input ? (0, classify_demand_1.classifyCaregiverDemand)(input.raw_input) : null;
    const featureEval = input.proposed_feature
        ? (0, evaluate_feature_1.evaluateFeatureAgainstNorthStar)(input.proposed_feature)
        : null;
    const implicit_output_coverage = coverImplicitOutputs(input);
    const coveredCount = Object.values(implicit_output_coverage).filter(Boolean).length;
    const output_answers_memory_questions = coveredCount >= 2;
    const feature_gate_passed = featureEval ? (0, evaluate_feature_1.isNorthStarPass)(featureEval.verdict) : true;
    const refused_generic_search_answer = demand?.demand_type === "search_demand";
    return {
        active: true,
        north_star: contract_constants_1.PRODUCT_NORTH_STAR,
        feature_gate_passed,
        demand,
        implicit_output_coverage,
        output_answers_memory_questions,
        anti_answer_engine: true,
        refused_generic_search_answer,
        success_criteria_message: "Caregivers stop re-explaining history; system surfaces what changed; decisions come from system memory.",
        rules_upheld: [...contract_constants_1.PRODUCT_NORTH_STAR_RULES],
        defining_principle: contract_constants_1.PRODUCT_NORTH_STAR_DEFINING_PRINCIPLE,
    };
}
