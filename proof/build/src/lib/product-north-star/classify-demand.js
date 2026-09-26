"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.classifyCaregiverDemand = classifyCaregiverDemand;
exports.resolveEnginesForQuestion = resolveEnginesForQuestion;
const demand_model_1 = require("./demand-model");
/**
 * Classify caregiver input as continuity demand vs search demand.
 * Continuity demand = product-market fit. Search demand = content, not core product.
 */
function classifyCaregiverDemand(rawInput) {
    const text = rawInput.trim();
    if (!text) {
        return {
            demand_type: "unknown",
            matched_themes: [],
            underlying_needs: [],
            build_engines_not_answers: [],
            treat_as_product_signal: false,
        };
    }
    const matched_themes = [];
    const underlying_needs = [];
    const engines = new Set();
    let themeContinuityHit = false;
    let themeSearchHit = false;
    for (const row of demand_model_1.QUESTION_TO_CONTINUITY_FAILURE) {
        const hit = row.example_questions.some((q) => {
            const key = q.toLowerCase().slice(0, 24);
            return text.toLowerCase().includes(key.slice(0, 16)) || fuzzyThemeHit(text, row.theme);
        });
        if (hit || fuzzyThemeHit(text, row.theme)) {
            matched_themes.push(row.theme);
            underlying_needs.push(row.underlying_need);
            for (const e of row.missing_capabilities)
                engines.add(e);
            if (row.demand_type === "continuity_demand")
                themeContinuityHit = true;
            if (row.demand_type === "search_demand")
                themeSearchHit = true;
        }
    }
    const searchHit = demand_model_1.SEARCH_DEMAND_PATTERNS.some((p) => p.test(text)) || themeSearchHit;
    const continuityHit = demand_model_1.CONTINUITY_DEMAND_PATTERNS.some((p) => p.test(text)) || themeContinuityHit;
    let demand_type = "unknown";
    // Pure search (Medicare/cost FAQ) must not become product-core "continuity".
    // Mixed input with real care events → prefer continuity demand.
    if (continuityHit && !searchHit)
        demand_type = "continuity_demand";
    else if (searchHit && !continuityHit)
        demand_type = "search_demand";
    else if (continuityHit && searchHit)
        demand_type = "continuity_demand";
    else if (themeSearchHit)
        demand_type = "search_demand";
    return {
        demand_type,
        matched_themes: [...new Set(matched_themes)],
        underlying_needs: [...new Set(underlying_needs)],
        build_engines_not_answers: [...engines],
        treat_as_product_signal: demand_type === "continuity_demand",
    };
}
function fuzzyThemeHit(text, theme) {
    switch (theme) {
        case "progression_visibility":
            return /\b(worse|worsening|declin|24\s*\/\s*7|professional care|wander|supervision)\b/i.test(text);
        case "decision_fatigue":
            return /\b(should i|what should i do|hire|memory care|when (?:to|should))\b/i.test(text);
        case "cognitive_load":
            return /\b(can'?t remember|forgetting|mixed up|tell the doctor|am i missing)\b/i.test(text);
        case "decision_confidence":
            return /\b(should i worry|am i doing enough|is this normal|missing something)\b/i.test(text);
        case "conversation_preparation":
            return /\b(tell the doctor|explain to (?:family|everyone)|what changed since)\b/i.test(text);
        case "care_coordination":
            return /\b(hire|caregiver|respite|live[- ]?in|night shift)\b/i.test(text);
        case "emotional_burden":
            return /\b(burnout|exhaust|guilt|balancing work)\b/i.test(text);
        case "financial_uncertainty":
            return /\b(medicare|medicaid|cost|afford|insurance)\b/i.test(text);
        default:
            return false;
    }
}
/**
 * Given a caregiver question, return engines to build — never an answer template.
 */
function resolveEnginesForQuestion(rawInput) {
    const demand = classifyCaregiverDemand(rawInput);
    return {
        do_not_build: "Longer AI explanation / Q&A answer template",
        build_instead: demand.build_engines_not_answers,
        underlying_need: demand.underlying_needs[0] ?? null,
    };
}
