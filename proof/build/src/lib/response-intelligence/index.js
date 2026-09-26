"use strict";
/**
 * Response Intelligence — meaning over language patterns.
 * SoT: docs/02-product/solenos-response-intelligence-directive.md
 * Output schema SoT: docs/02-product/solenos-response-contract.md
 */
Object.defineProperty(exports, "__esModule", { value: true });
exports.assertNoHardcodedScenarioBranch = exports.assertNoResponseContractNeverSay = exports.buildResponseContractOutput = exports.RESPONSE_CONTRACT_NEVER_SAY = exports.RESPONSE_CONTRACT_PIPELINE = exports.RESPONSE_CONTRACT_FIELDS = exports.RESPONSE_CONTRACT_PURPOSE = exports.containsAttentionScoreTheater = exports.shouldDiscloseAttentionLevel = exports.humanAttentionLabelFor = exports.ATTENTION_LABELS_BY_RISK = exports.inferRiskFromHeldCareEvidence = exports.RISK_FROM_EVIDENCE_PURPOSE = exports.buildResponseIntelligenceOutput = exports.evaluateGoldenSoftOrientation = exports.containsAiProductLanguage = exports.assertNoAiProductLanguage = exports.RESPONSE_HARD_FAILURE_CHECKS = exports.RESPONSE_GOLDEN_SOFT_INPUTS = exports.RESPONSE_AI_PRODUCT_LANGUAGE_BANS = exports.RESPONSE_OUTPUT_FIELDS = exports.RESPONSE_INTELLIGENCE_PIPELINE = exports.RESPONSE_INTELLIGENCE_PURPOSE = void 0;
var contract_constants_1 = require("./contract-constants");
Object.defineProperty(exports, "RESPONSE_INTELLIGENCE_PURPOSE", { enumerable: true, get: function () { return contract_constants_1.RESPONSE_INTELLIGENCE_PURPOSE; } });
Object.defineProperty(exports, "RESPONSE_INTELLIGENCE_PIPELINE", { enumerable: true, get: function () { return contract_constants_1.RESPONSE_INTELLIGENCE_PIPELINE; } });
Object.defineProperty(exports, "RESPONSE_OUTPUT_FIELDS", { enumerable: true, get: function () { return contract_constants_1.RESPONSE_OUTPUT_FIELDS; } });
Object.defineProperty(exports, "RESPONSE_AI_PRODUCT_LANGUAGE_BANS", { enumerable: true, get: function () { return contract_constants_1.RESPONSE_AI_PRODUCT_LANGUAGE_BANS; } });
Object.defineProperty(exports, "RESPONSE_GOLDEN_SOFT_INPUTS", { enumerable: true, get: function () { return contract_constants_1.RESPONSE_GOLDEN_SOFT_INPUTS; } });
Object.defineProperty(exports, "RESPONSE_HARD_FAILURE_CHECKS", { enumerable: true, get: function () { return contract_constants_1.RESPONSE_HARD_FAILURE_CHECKS; } });
var ai_product_language_1 = require("./ai-product-language");
Object.defineProperty(exports, "assertNoAiProductLanguage", { enumerable: true, get: function () { return ai_product_language_1.assertNoAiProductLanguage; } });
Object.defineProperty(exports, "containsAiProductLanguage", { enumerable: true, get: function () { return ai_product_language_1.containsAiProductLanguage; } });
var golden_soft_orientation_1 = require("./golden-soft-orientation");
Object.defineProperty(exports, "evaluateGoldenSoftOrientation", { enumerable: true, get: function () { return golden_soft_orientation_1.evaluateGoldenSoftOrientation; } });
var build_output_1 = require("./build-output");
Object.defineProperty(exports, "buildResponseIntelligenceOutput", { enumerable: true, get: function () { return build_output_1.buildResponseIntelligenceOutput; } });
var risk_from_evidence_1 = require("./risk-from-evidence");
Object.defineProperty(exports, "RISK_FROM_EVIDENCE_PURPOSE", { enumerable: true, get: function () { return risk_from_evidence_1.RISK_FROM_EVIDENCE_PURPOSE; } });
Object.defineProperty(exports, "inferRiskFromHeldCareEvidence", { enumerable: true, get: function () { return risk_from_evidence_1.inferRiskFromHeldCareEvidence; } });
var attention_label_1 = require("./attention-label");
Object.defineProperty(exports, "ATTENTION_LABELS_BY_RISK", { enumerable: true, get: function () { return attention_label_1.ATTENTION_LABELS_BY_RISK; } });
Object.defineProperty(exports, "humanAttentionLabelFor", { enumerable: true, get: function () { return attention_label_1.humanAttentionLabelFor; } });
Object.defineProperty(exports, "shouldDiscloseAttentionLevel", { enumerable: true, get: function () { return attention_label_1.shouldDiscloseAttentionLevel; } });
Object.defineProperty(exports, "containsAttentionScoreTheater", { enumerable: true, get: function () { return attention_label_1.containsAttentionScoreTheater; } });
var response_contract_1 = require("../response-contract");
Object.defineProperty(exports, "RESPONSE_CONTRACT_PURPOSE", { enumerable: true, get: function () { return response_contract_1.RESPONSE_CONTRACT_PURPOSE; } });
Object.defineProperty(exports, "RESPONSE_CONTRACT_FIELDS", { enumerable: true, get: function () { return response_contract_1.RESPONSE_CONTRACT_FIELDS; } });
Object.defineProperty(exports, "RESPONSE_CONTRACT_PIPELINE", { enumerable: true, get: function () { return response_contract_1.RESPONSE_CONTRACT_PIPELINE; } });
Object.defineProperty(exports, "RESPONSE_CONTRACT_NEVER_SAY", { enumerable: true, get: function () { return response_contract_1.RESPONSE_CONTRACT_NEVER_SAY; } });
Object.defineProperty(exports, "buildResponseContractOutput", { enumerable: true, get: function () { return response_contract_1.buildResponseContractOutput; } });
Object.defineProperty(exports, "assertNoResponseContractNeverSay", { enumerable: true, get: function () { return response_contract_1.assertNoResponseContractNeverSay; } });
Object.defineProperty(exports, "assertNoHardcodedScenarioBranch", { enumerable: true, get: function () { return response_contract_1.assertNoHardcodedScenarioBranch; } });
