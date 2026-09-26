"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.containsAiProductLanguage = containsAiProductLanguage;
exports.assertNoAiProductLanguage = assertNoAiProductLanguage;
const contract_constants_1 = require("./contract-constants");
function containsAiProductLanguage(text) {
    const lower = text.toLowerCase();
    return contract_constants_1.RESPONSE_AI_PRODUCT_LANGUAGE_BANS.some((p) => lower.includes(p));
}
function assertNoAiProductLanguage(parts, label = "response") {
    const blob = parts.filter(Boolean).join("\n");
    if (containsAiProductLanguage(blob)) {
        throw new Error(`Response Intelligence: AI product language leaked in ${label}: ${blob.slice(0, 200)}`);
    }
}
