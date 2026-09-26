"use strict";
/**
 * MVP Research Validation — cognitive load reduction + retention hypothesis.
 *
 * SolenOS is an external memory layer for care reality, not a productivity tool.
 * SoT: docs/02-product/solenos-mvp-research-validation.md
 */
Object.defineProperty(exports, "__esModule", { value: true });
exports.RETENTION_MICRO_PROMPT_STATUS = exports.getRetentionResearchStore = exports.resetRetentionResearchStore = exports.weekKeyFromIso = exports.aggregateWeeklyRetentionCohortMetrics = exports.attachFeedbackToRetentionResearch = exports.recordRetentionResearchEvent = exports.deriveRetentionProxySignals = exports.composeMentalLoadCaptureLines = exports.formatCompetingSituationLines = exports.prioritizeCompetingAttention = exports.evaluateAgainstResearchValidation = exports.RESEARCH_MVP_MUST_CREATE = exports.RESEARCH_ENGINEERING_PRIORITY = exports.RESEARCH_DO_NOT_BUILD_NOW = exports.RESEARCH_BUILD_NOW = exports.RESEARCH_SUCCESS_FEEL = exports.RESEARCH_RETENTION_HYPOTHESIS = exports.RESEARCH_VALIDATION_PURPOSE = void 0;
var contract_constants_1 = require("./contract-constants");
Object.defineProperty(exports, "RESEARCH_VALIDATION_PURPOSE", { enumerable: true, get: function () { return contract_constants_1.RESEARCH_VALIDATION_PURPOSE; } });
Object.defineProperty(exports, "RESEARCH_RETENTION_HYPOTHESIS", { enumerable: true, get: function () { return contract_constants_1.RESEARCH_RETENTION_HYPOTHESIS; } });
Object.defineProperty(exports, "RESEARCH_SUCCESS_FEEL", { enumerable: true, get: function () { return contract_constants_1.RESEARCH_SUCCESS_FEEL; } });
Object.defineProperty(exports, "RESEARCH_BUILD_NOW", { enumerable: true, get: function () { return contract_constants_1.RESEARCH_BUILD_NOW; } });
Object.defineProperty(exports, "RESEARCH_DO_NOT_BUILD_NOW", { enumerable: true, get: function () { return contract_constants_1.RESEARCH_DO_NOT_BUILD_NOW; } });
Object.defineProperty(exports, "RESEARCH_ENGINEERING_PRIORITY", { enumerable: true, get: function () { return contract_constants_1.RESEARCH_ENGINEERING_PRIORITY; } });
Object.defineProperty(exports, "RESEARCH_MVP_MUST_CREATE", { enumerable: true, get: function () { return contract_constants_1.RESEARCH_MVP_MUST_CREATE; } });
var evaluate_feature_1 = require("./evaluate-feature");
Object.defineProperty(exports, "evaluateAgainstResearchValidation", { enumerable: true, get: function () { return evaluate_feature_1.evaluateAgainstResearchValidation; } });
var competing_attention_1 = require("./competing-attention");
Object.defineProperty(exports, "prioritizeCompetingAttention", { enumerable: true, get: function () { return competing_attention_1.prioritizeCompetingAttention; } });
Object.defineProperty(exports, "formatCompetingSituationLines", { enumerable: true, get: function () { return competing_attention_1.formatCompetingSituationLines; } });
var mental_load_capture_1 = require("./mental-load-capture");
Object.defineProperty(exports, "composeMentalLoadCaptureLines", { enumerable: true, get: function () { return mental_load_capture_1.composeMentalLoadCaptureLines; } });
var retention_instrumentation_1 = require("./retention-instrumentation");
Object.defineProperty(exports, "deriveRetentionProxySignals", { enumerable: true, get: function () { return retention_instrumentation_1.deriveRetentionProxySignals; } });
Object.defineProperty(exports, "recordRetentionResearchEvent", { enumerable: true, get: function () { return retention_instrumentation_1.recordRetentionResearchEvent; } });
Object.defineProperty(exports, "attachFeedbackToRetentionResearch", { enumerable: true, get: function () { return retention_instrumentation_1.attachFeedbackToRetentionResearch; } });
Object.defineProperty(exports, "aggregateWeeklyRetentionCohortMetrics", { enumerable: true, get: function () { return retention_instrumentation_1.aggregateWeeklyRetentionCohortMetrics; } });
Object.defineProperty(exports, "weekKeyFromIso", { enumerable: true, get: function () { return retention_instrumentation_1.weekKeyFromIso; } });
Object.defineProperty(exports, "resetRetentionResearchStore", { enumerable: true, get: function () { return retention_instrumentation_1.resetRetentionResearchStore; } });
Object.defineProperty(exports, "getRetentionResearchStore", { enumerable: true, get: function () { return retention_instrumentation_1.getRetentionResearchStore; } });
Object.defineProperty(exports, "RETENTION_MICRO_PROMPT_STATUS", { enumerable: true, get: function () { return retention_instrumentation_1.RETENTION_MICRO_PROMPT_STATUS; } });
