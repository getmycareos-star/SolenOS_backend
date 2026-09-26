"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.TELEMETRY_RESPONSE_HEADERS = exports.TelemetryAnalyzeRequestExtensionSchema = exports.GroundingContextPackageSchema = exports.TelemetryFeedbackSubmitSchema = exports.TelemetryInteractionInsertSchema = exports.SolenOSOutputSchema = exports.PolicyFactRowSchema = exports.KnowledgeChunkRowSchema = exports.InteractionContextRowSchema = exports.DocumentEvidenceRowSchema = exports.TelemetryUserRowSchema = exports.TelemetryUserIdSchema = void 0;
exports.assertUserSchemaBoundary = assertUserSchemaBoundary;
exports.assertInteractionSchemaBoundary = assertInteractionSchemaBoundary;
exports.assertFeedbackSchemaBoundary = assertFeedbackSchemaBoundary;
const zod_1 = require("zod");
const multilingual_execution_1 = require("../multilingual-execution");
const contract_constants_1 = require("./contract-constants");
const risk_levels_1 = require("../implementation-enforcement/risk-levels");
const contract_constants_2 = require("../caregiver-depletion-signals/contract-constants");
const contract_constants_3 = require("../post-care-insight/contract-constants");
const constants_1 = require("../relief-validation/constants");
const contract_constants_4 = require("../relief-validation/contract-constants");
exports.TelemetryUserIdSchema = zod_1.z.string().uuid();
exports.TelemetryUserRowSchema = zod_1.z
    .object({
    id: exports.TelemetryUserIdSchema,
    created_at: zod_1.z.string(),
    last_seen_at: zod_1.z.string(),
    total_sessions: zod_1.z.number().int().min(0),
    auth_enabled: zod_1.z.boolean().default(false),
    email: zod_1.z.string().email().nullable().optional(),
    password_hash: zod_1.z.string().nullable().optional(),
    language_preference: zod_1.z.enum(multilingual_execution_1.SOLENOS_LANGUAGES).default("en"),
    ui_language: zod_1.z.enum(multilingual_execution_1.SOLENOS_LANGUAGES).default("en"),
    voice_language: zod_1.z.enum(multilingual_execution_1.SOLENOS_LANGUAGES).default("en"),
})
    .strict();
exports.DocumentEvidenceRowSchema = zod_1.z
    .object({
    id: zod_1.z.string().uuid(),
    user_id: exports.TelemetryUserIdSchema,
    file_url: zod_1.z.string().min(1),
    extracted_text: zod_1.z.string().nullable(),
    structured_output: zod_1.z.unknown().nullable(),
    created_at: zod_1.z.string(),
})
    .strict();
exports.InteractionContextRowSchema = zod_1.z
    .object({
    id: zod_1.z.string().uuid(),
    input_raw: zod_1.z.string(),
    output_structured: zod_1.z.unknown(),
    risk_level: zod_1.z.string(),
    created_at: zod_1.z.string(),
})
    .strict();
exports.KnowledgeChunkRowSchema = zod_1.z
    .object({
    id: zod_1.z.string().uuid(),
    chunk: zod_1.z.string(),
    category: zod_1.z.string().nullable(),
    source: zod_1.z.string().nullable(),
})
    .strict();
exports.PolicyFactRowSchema = zod_1.z
    .object({
    id: zod_1.z.string().uuid(),
    category: zod_1.z.string(),
    key: zod_1.z.string(),
    value: zod_1.z.unknown(),
    last_updated: zod_1.z.string(),
})
    .strict();
exports.SolenOSOutputSchema = zod_1.z
    .object({
    what_is_happening: zod_1.z.string(),
    what_matters_now: zod_1.z.string(),
    what_to_ask_next: zod_1.z.string(),
    risk_level: zod_1.z.enum(risk_levels_1.SOLENOS_RISK_LEVELS),
    what_can_wait: zod_1.z.string(),
})
    .strict();
exports.TelemetryInteractionInsertSchema = zod_1.z
    .object({
    user_id: exports.TelemetryUserIdSchema,
    input_raw: zod_1.z.string().min(1),
    output_structured: exports.SolenOSOutputSchema,
    risk_level: zod_1.z.enum(risk_levels_1.SOLENOS_RISK_LEVELS),
    latency_ms: zod_1.z.number().int().min(0),
    structure_valid: zod_1.z.boolean(),
    semantic_valid: zod_1.z.boolean(),
    input_category: zod_1.z.enum(constants_1.INPUT_CATEGORIES),
    relief_outcome: zod_1.z.enum(contract_constants_4.RELIEF_OUTCOMES),
    requery_detected: zod_1.z.boolean(),
    helpful_feedback: zod_1.z.boolean().nullable(),
    relief_signal: zod_1.z.number().min(0).max(1).nullable().optional(),
    helpful_yes_no: zod_1.z.boolean().nullable().optional(),
    reduced_confusion_yes_no: zod_1.z.boolean().nullable().optional(),
    care_context_state: zod_1.z.enum(contract_constants_3.CARE_CONTEXT_STATES),
    caregiver_depletion_state: zod_1.z.enum(contract_constants_2.CAREGIVER_DEPLETION_STATES),
    is_single_caregiver: zod_1.z.boolean(),
    environmental_dependency_flag: zod_1.z.enum(contract_constants_2.ENVIRONMENTAL_DEPENDENCY_FLAGS),
})
    .strict();
exports.TelemetryFeedbackSubmitSchema = zod_1.z
    .object({
    interaction_id: zod_1.z.string().uuid(),
    helpful_yes_no: zod_1.z.boolean(),
    reduced_confusion_yes_no: zod_1.z.boolean(),
    /** Durable care key — enables one-turn load/containment after confusion feedback only. */
    care_key: zod_1.z.string().min(1).optional(),
})
    .strict();
exports.GroundingContextPackageSchema = zod_1.z
    .object({
    document_evidence: zod_1.z.array(zod_1.z.object({
        extracted_text: zod_1.z.string().nullable(),
        structured_output: zod_1.z.unknown().nullable(),
    })),
    interaction_context: zod_1.z.array(zod_1.z.object({
        input_raw: zod_1.z.string(),
        risk_level: zod_1.z.string(),
        created_at: zod_1.z.string(),
    })),
    knowledge_chunks: zod_1.z.array(zod_1.z.object({
        chunk: zod_1.z.string(),
        category: zod_1.z.string().nullable(),
        source: zod_1.z.string().nullable(),
    })),
    policy_facts: zod_1.z.array(zod_1.z.object({
        category: zod_1.z.string(),
        key: zod_1.z.string(),
        value: zod_1.z.unknown(),
    })),
    memory_influence_envelope: zod_1.z
        .object({
        compositeInfluence: zod_1.z.number(),
        hints: zod_1.z.array(zod_1.z.string()),
    })
        .optional(),
})
    .strict();
exports.TelemetryAnalyzeRequestExtensionSchema = zod_1.z.object({
    telemetry_user_id: exports.TelemetryUserIdSchema.optional(),
    prior_input_raw: zod_1.z.string().optional(),
});
exports.TELEMETRY_RESPONSE_HEADERS = {
    userId: "x-solenos-telemetry-user-id",
    interactionId: "x-solenos-telemetry-interaction-id",
};
function assertUserSchemaBoundary(columns) {
    const allowed = new Set([
        ...contract_constants_1.TELEMETRY_USER_REQUIRED_FIELDS,
        ...contract_constants_1.TELEMETRY_USER_OPTIONAL_FIELDS,
        ...contract_constants_1.TELEMETRY_USER_DEPRECATED_FIELDS,
    ]);
    for (const column of columns) {
        if (contract_constants_1.TELEMETRY_USER_FORBIDDEN_FIELDS.includes(column)) {
            throw new Error(`forbidden user column: ${column}`);
        }
        if (!allowed.has(column)) {
            throw new Error(`user schema drift — disallowed column: ${column}`);
        }
    }
}
function assertInteractionSchemaBoundary(fields) {
    const allowed = new Set([
        ...contract_constants_1.TELEMETRY_INTERACTION_REQUIRED_FIELDS,
        "id",
        "created_at",
    ]);
    for (const field of fields) {
        if (!allowed.has(field)) {
            throw new Error(`interaction schema drift — disallowed field: ${field}`);
        }
    }
}
function assertFeedbackSchemaBoundary(fields) {
    const allowed = new Set([
        ...contract_constants_1.TELEMETRY_FEEDBACK_REQUIRED_FIELDS,
        "id",
        "created_at",
        "user_id",
    ]);
    for (const field of fields) {
        if (!allowed.has(field)) {
            throw new Error(`feedback schema drift — disallowed field: ${field}`);
        }
    }
}
