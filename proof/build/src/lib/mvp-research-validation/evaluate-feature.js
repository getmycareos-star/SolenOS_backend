"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.evaluateAgainstResearchValidation = evaluateAgainstResearchValidation;
const contract_constants_1 = require("./contract-constants");
const product_north_star_1 = require("../product-north-star");
const REJECT_PATTERNS = [
    /\bmarketplace\b/i,
    /\bresource directory\b/i,
    /\btraining platform\b/i,
    /\bcaregiver (?:training|education) (?:app|platform|course)\b/i,
    /\bfinancial (?:assistance|aid|planning) platform\b/i,
    /\bmedical advice engine\b/i,
    /\bcare coordinator replacement\b/i,
    /\btask manager\b/i,
    /\bchecklist app\b/i,
    /\bproductivity tool\b/i,
    /\bdocument (?:summarizer|analyzer)\b/i,
    /\bupload.{0,40}summary\b/i,
    /\bgeneric dementia (?:advice|tips|FAQ)\b/i,
];
const PASS_PATTERNS = [
    /\bcognitive load\b/i,
    /\bmental load\b/i,
    /\bexternal memory\b/i,
    /\bcare reality\b/i,
    /\bdecision memory\b/i,
    /\bsituation relationship\b/i,
    /\bwhat matters now\b/i,
    /\bunknowns?\b/i,
    /\bcontinuity\b/i,
    /\bevidence link/i,
    /\bchange detection\b/i,
    /\bdocument.{0,20}care reality\b/i,
];
/**
 * Gate features against research validation: reduce cognitive load, prove retention value.
 */
function evaluateAgainstResearchValidation(featureDescription) {
    const text = featureDescription.trim();
    if (!text) {
        return {
            feature_description: text,
            verdict: "unclear_rejected",
            reduces_cognitive_load: null,
            reason: "Empty feature — research validation requires a clear cognitive-load claim.",
        };
    }
    if (REJECT_PATTERNS.some((p) => p.test(text))) {
        return {
            feature_description: text,
            verdict: "reject",
            reduces_cognitive_load: false,
            reason: `${contract_constants_1.RESEARCH_VALIDATION_PURPOSE} Feature expands away from external memory / continuity.`,
        };
    }
    for (const banned of contract_constants_1.RESEARCH_DO_NOT_BUILD_NOW) {
        const loose = banned.replace(/_/g, "[ _-]?");
        if (new RegExp(loose, "i").test(text)) {
            return {
                feature_description: text,
                verdict: "reject",
                reduces_cognitive_load: false,
                reason: `Out of MVP boundary (${banned}). Prove mental-load reduction first.`,
            };
        }
    }
    const north = (0, product_north_star_1.evaluateFeatureAgainstNorthStar)(text);
    if (north.verdict === "reject" || north.verdict === "unclear_rejected") {
        return {
            feature_description: text,
            verdict: north.verdict,
            reduces_cognitive_load: false,
            reason: north.reason,
        };
    }
    if (PASS_PATTERNS.some((p) => p.test(text))) {
        return {
            feature_description: text,
            verdict: "pass",
            reduces_cognitive_load: true,
            reason: "Strengthens external memory / continuity / mental-load reduction.",
        };
    }
    if (north.verdict === "pass") {
        return {
            feature_description: text,
            verdict: "pass",
            reduces_cognitive_load: true,
            reason: north.reason,
        };
    }
    return {
        feature_description: text,
        verdict: "unclear_rejected",
        reduces_cognitive_load: null,
        reason: "Unclear whether this reduces cognitive load or only stores information.",
    };
}
