"use strict";
/**
 * PostgreSQL Implementation Contract — evidence ledger for cognitive decompression ONLY.
 * NOT caregiver memory, longitudinal tracking, or personalization.
 */
Object.defineProperty(exports, "__esModule", { value: true });
exports.POSTGRES_VECTOR_DIMENSIONS = exports.POSTGRES_KNOWLEDGE_CHUNK_LIMIT = exports.POSTGRES_INTERACTION_CONTEXT_LIMIT = exports.POSTGRES_POLICY_FACT_FIELDS = exports.POSTGRES_KNOWLEDGE_BASE_FIELDS = exports.POSTGRES_DOCUMENT_FIELDS = exports.POSTGRES_INTERACTION_LEGACY_FIELDS = exports.POSTGRES_INTERACTION_RELIEF_FIELDS = exports.POSTGRES_INTERACTION_LEDGER_FIELDS = exports.POSTGRES_USER_FORBIDDEN_FIELDS = exports.POSTGRES_USER_DEPRECATED_FIELDS = exports.POSTGRES_USER_OPTIONAL_FIELDS = exports.POSTGRES_USER_REQUIRED_FIELDS = exports.POSTGRES_INVARIANTS = exports.POSTGRES_FORBIDDEN_USES = exports.POSTGRES_PRE_REASONING_PIPELINE = exports.POSTGRES_CONTRACT_TABLES = exports.POSTGRES_MODULE_TABLE_MAP = exports.POSTGRES_CONTRACT_MODULES = exports.POSTGRES_CONTRACT_ONE_LINE_TRUTH = exports.POSTGRES_CONTRACT_IDENTITY = void 0;
exports.POSTGRES_CONTRACT_IDENTITY = "a constrained evidence and interaction ledger for cognitive decompression, document grounding, and safety validation";
exports.POSTGRES_CONTRACT_ONE_LINE_TRUTH = "PostgreSQL stores interaction evidence, document extraction, grounding retrieval, and policy facts — never caregiver memory, longitudinal profiles, or behavioral analytics.";
exports.POSTGRES_CONTRACT_MODULES = [
    "identity_layer",
    "document_layer",
    "interaction_ledger",
    "grounding_layer",
    "safety_validation_layer",
];
exports.POSTGRES_MODULE_TABLE_MAP = {
    identity_layer: ["users"],
    document_layer: ["documents"],
    interaction_ledger: ["interactions", "feedback"],
    grounding_layer: ["knowledge_base"],
    safety_validation_layer: ["policy_facts"],
};
exports.POSTGRES_CONTRACT_TABLES = [
    "users",
    "documents",
    "interactions",
    "feedback",
    "knowledge_base",
    "policy_facts",
];
exports.POSTGRES_PRE_REASONING_PIPELINE = [
    "LOAD_DOCUMENT_EVIDENCE",
    "LOAD_INTERACTION_CONTEXT",
    "VECTOR_RETRIEVAL",
    "POLICY_RETRIEVAL",
    "CONTEXT_PACKAGING",
];
exports.POSTGRES_FORBIDDEN_USES = [
    "caregiver memory",
    "longitudinal patient tracking",
    "behavioral analytics",
    "care coordination database",
    "medical records store",
    "personalization engine",
    "user scoring",
    "trust_score decisions",
    "engagement optimization",
    "retention optimization",
];
exports.POSTGRES_INVARIANTS = [
    "Persistence does NOT expand product capability — it validates relief and grounds reasoning.",
    "Pre-reasoning loads evidence only; never raw DB structure or inferred caregiver history.",
    "Interaction context is limited recent ledger rows — NOT longitudinal profiling.",
    "users table holds identity only — no demographics or behavioral expansion.",
    "RLS isolates user-scoped rows when Supabase auth.uid() is available; app uses backend service key.",
];
exports.POSTGRES_USER_REQUIRED_FIELDS = [
    "id",
    "created_at",
    "last_seen_at",
    "total_sessions",
    "auth_enabled",
];
exports.POSTGRES_USER_OPTIONAL_FIELDS = [
    "email",
    "password_hash",
    "language_preference",
    "ui_language",
    "voice_language",
];
/** Deprecated column retained for migration compatibility — must not be used in product logic. */
exports.POSTGRES_USER_DEPRECATED_FIELDS = ["trust_score"];
exports.POSTGRES_USER_FORBIDDEN_FIELDS = [
    "name",
    "phone",
    "demographics",
    "medical_data",
    "care_relationships",
    "behavioral_profiles",
    "personalization_attributes",
];
exports.POSTGRES_INTERACTION_LEDGER_FIELDS = [
    "user_id",
    "input_raw",
    "output_structured",
    "risk_level",
    "relief_signal",
    "latency_ms",
    "structure_valid",
    "created_at",
];
exports.POSTGRES_INTERACTION_RELIEF_FIELDS = [
    "helpful_yes_no",
    "reduced_confusion_yes_no",
];
/** Legacy relief-validation columns — retained for backward compatibility. */
exports.POSTGRES_INTERACTION_LEGACY_FIELDS = [
    "semantic_valid",
    "input_category",
    "relief_outcome",
    "requery_detected",
    "helpful_feedback",
];
exports.POSTGRES_DOCUMENT_FIELDS = [
    "id",
    "user_id",
    "file_url",
    "extracted_text",
    "structured_output",
    "created_at",
];
exports.POSTGRES_KNOWLEDGE_BASE_FIELDS = [
    "id",
    "chunk",
    "embedding",
    "category",
    "source",
];
exports.POSTGRES_POLICY_FACT_FIELDS = [
    "id",
    "category",
    "key",
    "value",
    "last_updated",
];
exports.POSTGRES_INTERACTION_CONTEXT_LIMIT = 5;
exports.POSTGRES_KNOWLEDGE_CHUNK_LIMIT = 10;
exports.POSTGRES_VECTOR_DIMENSIONS = 1536;
