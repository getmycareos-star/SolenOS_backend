"use strict";
/**
 * Evidence ledger persistence — aligned with PostgreSQL Implementation Contract.
 * Measurement + grounding retrieval ONLY — not caregiver memory or longitudinal tracking.
 */
Object.defineProperty(exports, "__esModule", { value: true });
exports.TELEMETRY_PERSISTENCE_PURPOSE = exports.TELEMETRY_DRIFT_PREVENTION_RULE = exports.TELEMETRY_EVENT_MODEL = exports.TELEMETRY_ALLOWED_TABLES = exports.TELEMETRY_FEEDBACK_REQUIRED_FIELDS = exports.TELEMETRY_CAREGIVER_DEPLETION_RULE = exports.TELEMETRY_CARE_CONTEXT_STATE_RULE = exports.TELEMETRY_INTERACTION_REQUIRED_FIELDS = exports.TELEMETRY_USER_FORBIDDEN_FIELDS = exports.TELEMETRY_USER_DEPRECATED_FIELDS = exports.TELEMETRY_USER_OPTIONAL_FIELDS = exports.TELEMETRY_USER_REQUIRED_FIELDS = exports.TELEMETRY_FORBIDDEN_IDENTITY_DRIFT = exports.TELEMETRY_FORBIDDEN_POSTGRES_USES = exports.TELEMETRY_POSTGRES_ROLE = exports.TELEMETRY_ARCHITECTURE_PRINCIPLE = exports.TELEMETRY_ONE_LINE_TRUTH = exports.TELEMETRY_IDENTITY = void 0;
const postgres_contract_1 = require("../postgres-contract");
exports.TELEMETRY_IDENTITY = "a stateless deterministic cognitive decompression engine with a minimal evidence ledger used ONLY to validate relief, ground reasoning, and store document extraction";
exports.TELEMETRY_ONE_LINE_TRUTH = postgres_contract_1.POSTGRES_CONTRACT_ONE_LINE_TRUTH;
exports.TELEMETRY_ARCHITECTURE_PRINCIPLE = "Persistence does NOT expand capability. It exists ONLY as an evidence ledger for cognitive relief validation and pre-reasoning grounding — NOT a system feature surface.";
exports.TELEMETRY_POSTGRES_ROLE = "an evidence ledger + grounding retrieval store for cognitive decompression and safety validation";
exports.TELEMETRY_FORBIDDEN_POSTGRES_USES = [
    ...postgres_contract_1.POSTGRES_FORBIDDEN_USES,
    "user modeling",
    "memory system",
    "care journey tracking",
    "conversation_history",
];
exports.TELEMETRY_FORBIDDEN_IDENTITY_DRIFT = [
    "healthcare system",
    "CRM",
    "care coordination platform",
    "patient management system",
    "longitudinal tracking system",
    "personalization engine",
    "assistant with memory",
];
exports.TELEMETRY_USER_REQUIRED_FIELDS = [
    "id",
    "created_at",
    "last_seen_at",
    "total_sessions",
    "auth_enabled",
];
exports.TELEMETRY_USER_OPTIONAL_FIELDS = [
    "email",
    "password_hash",
    "language_preference",
    "ui_language",
    "voice_language",
    "governance_settings",
];
/** @deprecated trust_score — retained in DB for migration compat; forbidden in application logic */
exports.TELEMETRY_USER_DEPRECATED_FIELDS = ["trust_score"];
exports.TELEMETRY_USER_FORBIDDEN_FIELDS = [
    "name",
    "phone",
    "demographics",
    "medical_data",
    "care_relationships",
    "behavioral_profiles",
    "personalization_attributes",
];
exports.TELEMETRY_INTERACTION_REQUIRED_FIELDS = [
    "user_id",
    "input_raw",
    "output_structured",
    "risk_level",
    "latency_ms",
    "structure_valid",
    "semantic_valid",
    "input_category",
    "relief_outcome",
    "requery_detected",
    "helpful_feedback",
    "relief_signal",
    "helpful_yes_no",
    "reduced_confusion_yes_no",
    "care_context_state",
    "caregiver_depletion_state",
    "is_single_caregiver",
    "environmental_dependency_flag",
];
/** care_context_state on interactions — observational label only, NOT profiling input. */
exports.TELEMETRY_CARE_CONTEXT_STATE_RULE = "care_context_state is persisted as a shallow surface-signal label for measurement — forbidden for user profiling, segmentation, or lifecycle routing.";
/** Caregiver depletion signals on interactions — observational labels only, NOT intervention input. */
exports.TELEMETRY_CAREGIVER_DEPLETION_RULE = "caregiver depletion signals are persisted as shallow surface-signal labels for measurement — forbidden for user profiling, segmentation, lifecycle routing, or intervention.";
exports.TELEMETRY_FEEDBACK_REQUIRED_FIELDS = [
    "interaction_id",
    "helpful_yes_no",
    "reduced_confusion_yes_no",
];
exports.TELEMETRY_ALLOWED_TABLES = [
    "users",
    "documents",
    "interactions",
    "feedback",
    "knowledge_base",
    "policy_facts",
];
exports.TELEMETRY_EVENT_MODEL = "INPUT → Pre-Reasoning Grounding → Cognitive Decomposition → Structured Output → Relief Validation";
exports.TELEMETRY_DRIFT_PREVENTION_RULE = "If Postgres is used for anything beyond evidence logging, grounding retrieval, or relief validation, SolenOS identity is lost.";
exports.TELEMETRY_PERSISTENCE_PURPOSE = [
    "did this system reduce confusion effectively?",
    "did structure work consistently?",
    "where does cognitive relief fail?",
    "what document evidence and policy facts ground this interaction?",
];
