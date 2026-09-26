"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.buildResponseIntelligenceOutput = buildResponseIntelligenceOutput;
const response_contract_1 = require("../response-contract");
const ai_product_language_1 = require("./ai-product-language");
/**
 * Map composer / ACS turn understanding into the Response Contract output.
 * Call only AFTER meaning is formed — never as a blank fill-in template.
 */
function buildResponseIntelligenceOutput(params) {
    const risk = params.risk_level ?? inferCalmRiskLevel(params);
    const output = (0, response_contract_1.buildResponseContractOutput)({
        what_is_happening: params.what_is_happening,
        what_matters_now: params.what_matters_now,
        what_to_ask_next: params.what_to_ask_next,
        what_can_wait: params.what_can_wait,
        follow_up_items: params.follow_up_items,
        risk_level: risk,
    });
    (0, ai_product_language_1.assertNoAiProductLanguage)([
        output.what_is_happening,
        output.what_matters_now,
        typeof output.what_to_ask_next === "string"
            ? output.what_to_ask_next
            : output.what_to_ask_next.join(" "),
        output.what_can_wait,
        ...output.follow_up_items,
    ], "response_intelligence_output");
    return output;
}
/** Conservative attention signal — never invent emergency from thin input or event kind. */
function inferCalmRiskLevel(params) {
    if (params.risk_level)
        return params.risk_level;
    // Meaningful change + open unknowns elevates attention — not event kind.
    if (params.has_meaningful_change && params.has_open_unknowns)
        return "medium";
    // Explicit first-observation thin capture stays low.
    if (typeof params.observation_count === "number" && params.observation_count <= 1) {
        return "low";
    }
    return "low";
}
