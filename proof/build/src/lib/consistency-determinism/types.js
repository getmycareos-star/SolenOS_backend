"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.DETERMINISM_FAILURE_TYPES = exports.DECISION_TRACE_FIELD_ORDER = exports.DecisionTraceSchema = exports.OutputMetaSchema = exports.META_FIELD_ORDER = exports.SOLENOS_FIELD_ORDER = void 0;
const zod_1 = require("zod");
const contract_1 = require("../canonical-architecture/contract");
exports.SOLENOS_FIELD_ORDER = contract_1.CANONICAL_OUTPUT_FIELD_ORDER;
/** @deprecated _meta removed from MVP schema — kept for legacy adapters only */
exports.META_FIELD_ORDER = [
    "context_completeness",
    "missing_critical_fact",
    "confidence",
];
/** @deprecated */
exports.OutputMetaSchema = zod_1.z
    .object({
    context_completeness: zod_1.z.number().min(0).max(1),
    missing_critical_fact: zod_1.z.string().nullable(),
    confidence: zod_1.z.enum(["low", "medium", "high", "unknown"]),
})
    .strict();
/** @deprecated Use OutputMetaSchema */
exports.DecisionTraceSchema = exports.OutputMetaSchema;
/** @deprecated Use META_FIELD_ORDER */
exports.DECISION_TRACE_FIELD_ORDER = exports.META_FIELD_ORDER;
exports.DETERMINISM_FAILURE_TYPES = [
    "CONSISTENCY_FAILURE",
    "STRUCTURE_DRIFT_DETECTED",
    "PRIORITY_DRIFT_DETECTED",
    "INTERPRETATION_DRIFT_DETECTED",
    "PROMPT_REGRESSION_FAILURE",
];
