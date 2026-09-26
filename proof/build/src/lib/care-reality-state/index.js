"use strict";
/**
 * Care Reality State public exports.
 *
 * Client-safe: contract-constants, types, disclosure helpers.
 * Server-only: re-exported from process (node:fs) — do not import this barrel from client UI.
 * Prefer `care-reality-state/disclosure` or `care-reality-state/types` from client modules.
 */
Object.defineProperty(exports, "__esModule", { value: true });
exports.projectDisclosureFromState = exports.clearCareRealityStateMemoryCache = exports.resetCareRealityStateStore = exports.clearCareRealityState = exports.updateCareRealityState = exports.getCareRealityState = exports.primaryScreenQuestionFor = exports.buildDisclosurePlan = exports.evaluateResponseEvolution = exports.disclosureStageFor = exports.COGNITIVE_LOAD_PRIMARY_QUESTIONS = exports.DISCLOSURE_SECTIONS_BY_STAGE = exports.CARE_REALITY_STATE_NEVER = exports.CARE_REALITY_FORBIDDEN_INTERNAL_QUESTION = exports.CARE_REALITY_INTERNAL_QUESTION = exports.CARE_REALITY_DISCLOSURE_STAGES = exports.CARE_REALITY_STATE_CHAIN = exports.CARE_REALITY_STATE_PURPOSE = exports.CARE_REALITY_STATE_IDENTITY = void 0;
var contract_constants_1 = require("./contract-constants");
Object.defineProperty(exports, "CARE_REALITY_STATE_IDENTITY", { enumerable: true, get: function () { return contract_constants_1.CARE_REALITY_STATE_IDENTITY; } });
Object.defineProperty(exports, "CARE_REALITY_STATE_PURPOSE", { enumerable: true, get: function () { return contract_constants_1.CARE_REALITY_STATE_PURPOSE; } });
Object.defineProperty(exports, "CARE_REALITY_STATE_CHAIN", { enumerable: true, get: function () { return contract_constants_1.CARE_REALITY_STATE_CHAIN; } });
Object.defineProperty(exports, "CARE_REALITY_DISCLOSURE_STAGES", { enumerable: true, get: function () { return contract_constants_1.CARE_REALITY_DISCLOSURE_STAGES; } });
Object.defineProperty(exports, "CARE_REALITY_INTERNAL_QUESTION", { enumerable: true, get: function () { return contract_constants_1.CARE_REALITY_INTERNAL_QUESTION; } });
Object.defineProperty(exports, "CARE_REALITY_FORBIDDEN_INTERNAL_QUESTION", { enumerable: true, get: function () { return contract_constants_1.CARE_REALITY_FORBIDDEN_INTERNAL_QUESTION; } });
Object.defineProperty(exports, "CARE_REALITY_STATE_NEVER", { enumerable: true, get: function () { return contract_constants_1.CARE_REALITY_STATE_NEVER; } });
Object.defineProperty(exports, "DISCLOSURE_SECTIONS_BY_STAGE", { enumerable: true, get: function () { return contract_constants_1.DISCLOSURE_SECTIONS_BY_STAGE; } });
Object.defineProperty(exports, "COGNITIVE_LOAD_PRIMARY_QUESTIONS", { enumerable: true, get: function () { return contract_constants_1.COGNITIVE_LOAD_PRIMARY_QUESTIONS; } });
var disclosure_1 = require("./disclosure");
Object.defineProperty(exports, "disclosureStageFor", { enumerable: true, get: function () { return disclosure_1.disclosureStageFor; } });
Object.defineProperty(exports, "evaluateResponseEvolution", { enumerable: true, get: function () { return disclosure_1.evaluateResponseEvolution; } });
Object.defineProperty(exports, "buildDisclosurePlan", { enumerable: true, get: function () { return disclosure_1.buildDisclosurePlan; } });
Object.defineProperty(exports, "primaryScreenQuestionFor", { enumerable: true, get: function () { return disclosure_1.primaryScreenQuestionFor; } });
var process_1 = require("./process");
Object.defineProperty(exports, "getCareRealityState", { enumerable: true, get: function () { return process_1.getCareRealityState; } });
Object.defineProperty(exports, "updateCareRealityState", { enumerable: true, get: function () { return process_1.updateCareRealityState; } });
Object.defineProperty(exports, "clearCareRealityState", { enumerable: true, get: function () { return process_1.clearCareRealityState; } });
Object.defineProperty(exports, "resetCareRealityStateStore", { enumerable: true, get: function () { return process_1.resetCareRealityStateStore; } });
Object.defineProperty(exports, "clearCareRealityStateMemoryCache", { enumerable: true, get: function () { return process_1.clearCareRealityStateMemoryCache; } });
Object.defineProperty(exports, "projectDisclosureFromState", { enumerable: true, get: function () { return process_1.projectDisclosureFromState; } });
