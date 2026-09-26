"use strict";
/**
 * Care Situation Understanding — public API.
 * SoT plan: Care Understanding Engine (instant value on first capture).
 */
Object.defineProperty(exports, "__esModule", { value: true });
exports.projectLlmUnderstanding = exports.deterministicUnderstanding = exports.llmStructuredUnderstanding = exports.acceptCareSituationUnderstanding = exports.projectCareSituationOrientation = exports.looksLikeFragmentationOrAdmin = exports.prioritizeCareSituation = exports.buildCareSituationUnderstandingFromExtraction = exports.buildCareSituationUnderstanding = exports.INSTANT_VALUE_RULE = exports.CARE_SITUATION_UNDERSTANDING_PURPOSE = void 0;
var types_1 = require("./types");
Object.defineProperty(exports, "CARE_SITUATION_UNDERSTANDING_PURPOSE", { enumerable: true, get: function () { return types_1.CARE_SITUATION_UNDERSTANDING_PURPOSE; } });
Object.defineProperty(exports, "INSTANT_VALUE_RULE", { enumerable: true, get: function () { return types_1.INSTANT_VALUE_RULE; } });
var build_1 = require("./build");
Object.defineProperty(exports, "buildCareSituationUnderstanding", { enumerable: true, get: function () { return build_1.buildCareSituationUnderstanding; } });
Object.defineProperty(exports, "buildCareSituationUnderstandingFromExtraction", { enumerable: true, get: function () { return build_1.buildCareSituationUnderstandingFromExtraction; } });
var prioritize_1 = require("./prioritize");
Object.defineProperty(exports, "prioritizeCareSituation", { enumerable: true, get: function () { return prioritize_1.prioritizeCareSituation; } });
Object.defineProperty(exports, "looksLikeFragmentationOrAdmin", { enumerable: true, get: function () { return prioritize_1.looksLikeFragmentationOrAdmin; } });
var project_1 = require("./project");
Object.defineProperty(exports, "projectCareSituationOrientation", { enumerable: true, get: function () { return project_1.projectCareSituationOrientation; } });
var acceptance_1 = require("./acceptance");
Object.defineProperty(exports, "acceptCareSituationUnderstanding", { enumerable: true, get: function () { return acceptance_1.acceptCareSituationUnderstanding; } });
var llm_understanding_1 = require("./llm-understanding");
Object.defineProperty(exports, "llmStructuredUnderstanding", { enumerable: true, get: function () { return llm_understanding_1.llmStructuredUnderstanding; } });
Object.defineProperty(exports, "deterministicUnderstanding", { enumerable: true, get: function () { return llm_understanding_1.deterministicUnderstanding; } });
var llm_integration_1 = require("./llm-integration");
Object.defineProperty(exports, "projectLlmUnderstanding", { enumerable: true, get: function () { return llm_integration_1.projectLlmUnderstanding; } });
