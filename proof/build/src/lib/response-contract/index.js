"use strict";
/**
 * SolenOS Response Contract (MVP).
 * SoT: docs/02-product/solenos-response-contract.md
 *
 * Structured orientation from evidence — never a chatbot template.
 * Voice is FUTURE; same contract when it lands.
 */
Object.defineProperty(exports, "__esModule", { value: true });
exports.disclosurePlanFromReliefOnly = exports.applyReliefFieldsToDisclosurePlan = exports.mergeReliefIntoDisclosurePlan = exports.DISCLOSURE_MERGE_PURPOSE = exports.decideReliefDisclosure = exports.RELIEF_DECISION_PURPOSE = exports.RESPONSE_CONTRACT_MAX_ASKS = exports.RESPONSE_CONTRACT_NO_HARDCODED_EXAMPLES = exports.RESPONSE_CONTRACT_NON_NEGOTIABLE = exports.RESPONSE_CONTRACT_FAILURE_FEEL = exports.RESPONSE_CONTRACT_SUCCESS = exports.RESPONSE_CONTRACT_NEVER_SAY = exports.RESPONSE_RISK_LEVELS = exports.RESPONSE_CONTRACT_PIPELINE = exports.RESPONSE_CONTRACT_FIELDS = exports.RESPONSE_CONTRACT_NOT = exports.RESPONSE_CONTRACT_PURPOSE = void 0;
exports.isResponseContractRiskLevel = isResponseContractRiskLevel;
exports.containsResponseContractNeverSay = containsResponseContractNeverSay;
exports.assertNoResponseContractNeverSay = assertNoResponseContractNeverSay;
exports.normalizeContractAsks = normalizeContractAsks;
exports.buildResponseContractOutput = buildResponseContractOutput;
exports.assertNoHardcodedScenarioBranch = assertNoHardcodedScenarioBranch;
exports.RESPONSE_CONTRACT_PURPOSE = "Reduce uncertainty by maintaining an evolving understanding of one person's Care Reality.";
exports.RESPONSE_CONTRACT_NOT = [
    "ai_chatbot",
    "document_summarizer",
    "medical_advice_engine",
];
/** Ordered fields — engine always forms these; UI may disclose by maturity. */
exports.RESPONSE_CONTRACT_FIELDS = [
    "what_is_happening",
    "what_matters_now",
    "what_to_ask_next",
    "risk_level",
    "what_can_wait",
    "follow_up_items",
];
exports.RESPONSE_CONTRACT_PIPELINE = [
    "input",
    "evidence_understanding",
    "care_reality_update",
    "situation_relationship_engine",
    "response_contract",
];
exports.RESPONSE_RISK_LEVELS = ["low", "medium", "high"];
/** Caregiver-visible never-say (Response Contract). */
exports.RESPONSE_CONTRACT_NEVER_SAY = [
    "i understand how you feel",
    "i'm here for you",
    "im here for you",
    "based on my analysis",
    "according to the uploaded document",
    "i extracted",
    "ocr completed",
    "confidence score",
    "ai thinks",
    "i recommend",
    "it appears diagnosed",
    "as an ai",
    "chatgpt",
];
exports.RESPONSE_CONTRACT_SUCCESS = "I understand this situation better.";
exports.RESPONSE_CONTRACT_FAILURE_FEEL = "The AI summarized my note.";
exports.RESPONSE_CONTRACT_NON_NEGOTIABLE = "Every response must reduce uncertainty, preserve continuity, and maintain the Living Care Record.";
/**
 * Illustrations in docs/tests must never become product templates.
 * Verify scripts may use soft inputs; production composers must derive from evidence.
 */
exports.RESPONSE_CONTRACT_NO_HARDCODED_EXAMPLES = "Design scenarios are illustrations only — never canned responses in code.";
function isResponseContractRiskLevel(value) {
    return exports.RESPONSE_RISK_LEVELS.includes(value);
}
function containsResponseContractNeverSay(text) {
    const lower = text.toLowerCase();
    return exports.RESPONSE_CONTRACT_NEVER_SAY.some((p) => lower.includes(p));
}
function assertNoResponseContractNeverSay(parts, label = "response") {
    const blob = parts.filter(Boolean).join("\n");
    if (containsResponseContractNeverSay(blob)) {
        throw new Error(`Response Contract never-say leaked in ${label}: ${blob.slice(0, 200)}`);
    }
}
/** Max asks — usually one; never an interview. */
exports.RESPONSE_CONTRACT_MAX_ASKS = 3;
function normalizeContractAsks(next) {
    if (next == null)
        return [];
    if (typeof next === "string") {
        const t = next.trim();
        return t ? [t] : [];
    }
    return next.map((s) => s.trim()).filter(Boolean).slice(0, exports.RESPONSE_CONTRACT_MAX_ASKS);
}
/**
 * Build contract output from understanding already formed.
 * Never call as a blank fill-in template.
 */
function buildResponseContractOutput(params) {
    const asks = normalizeContractAsks(params.what_to_ask_next);
    const risk = params.risk_level ?? "low";
    const output = {
        what_is_happening: (params.what_is_happening ?? "").trim(),
        what_matters_now: (params.what_matters_now ?? "").trim(),
        what_to_ask_next: asks.length <= 1 ? (asks[0] ?? "") : asks,
        risk_level: risk,
        what_can_wait: (params.what_can_wait ?? "").trim(),
        follow_up_items: (params.follow_up_items ?? [])
            .map((s) => s.trim())
            .filter(Boolean)
            .slice(0, 5),
    };
    assertNoResponseContractNeverSay([
        output.what_is_happening,
        output.what_matters_now,
        typeof output.what_to_ask_next === "string"
            ? output.what_to_ask_next
            : output.what_to_ask_next.join(" "),
        output.what_can_wait,
        ...output.follow_up_items,
    ], "response_contract_output");
    return output;
}
/** Guard: scenario illustrations must not drive production branching. */
function assertNoHardcodedScenarioBranch(usedAsProductLogic) {
    if (usedAsProductLogic) {
        throw new Error("Response Contract: design-doc scenarios must never become templates or canned responses.");
    }
}
var relief_decision_1 = require("./relief-decision");
Object.defineProperty(exports, "RELIEF_DECISION_PURPOSE", { enumerable: true, get: function () { return relief_decision_1.RELIEF_DECISION_PURPOSE; } });
Object.defineProperty(exports, "decideReliefDisclosure", { enumerable: true, get: function () { return relief_decision_1.decideReliefDisclosure; } });
var disclosure_merge_1 = require("./disclosure-merge");
Object.defineProperty(exports, "DISCLOSURE_MERGE_PURPOSE", { enumerable: true, get: function () { return disclosure_merge_1.DISCLOSURE_MERGE_PURPOSE; } });
Object.defineProperty(exports, "mergeReliefIntoDisclosurePlan", { enumerable: true, get: function () { return disclosure_merge_1.mergeReliefIntoDisclosurePlan; } });
Object.defineProperty(exports, "applyReliefFieldsToDisclosurePlan", { enumerable: true, get: function () { return disclosure_merge_1.applyReliefFieldsToDisclosurePlan; } });
Object.defineProperty(exports, "disclosurePlanFromReliefOnly", { enumerable: true, get: function () { return disclosure_merge_1.disclosurePlanFromReliefOnly; } });
