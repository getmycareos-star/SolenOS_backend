"use strict";
/** Engine Execution Contract — engines propose transformations; they never own state. */
Object.defineProperty(exports, "__esModule", { value: true });
exports.REGISTERED_ENGINE_CONTRACTS = exports.FORBIDDEN_ENGINE_ACTIONS = exports.ENGINE_CONTRACT_RULES = exports.MUTATION_AUTHORITY = exports.EXECUTION_TIMINGS = exports.EXECUTION_TYPES = exports.ENGINE_EXECUTION_CONTRACT_DEFINING_PRINCIPLE = exports.ENGINE_EXECUTION_CONTRACT_IDENTITY = void 0;
exports.ENGINE_EXECUTION_CONTRACT_IDENTITY = "Engines do not define truth. Engines propose transformations of truth. Only the event-sourced system defines reality.";
exports.ENGINE_EXECUTION_CONTRACT_DEFINING_PRINCIPLE = "Engines do not own state. Engines only produce outputs.";
exports.EXECUTION_TYPES = ["deterministic", "probabilistic"];
exports.EXECUTION_TIMINGS = ["sync", "async"];
exports.MUTATION_AUTHORITY = "emit_only";
exports.ENGINE_CONTRACT_RULES = [
    "declare_input_schema",
    "declare_output_schema",
    "declare_execution_type",
    "declare_execution_timing",
    "no_direct_care_context_mutation",
    "outputs_must_be_traceable",
    "engine_isolation",
    "replayable_from_event_store",
];
exports.FORBIDDEN_ENGINE_ACTIONS = [
    "directly_edit_care_context",
    "overwrite_historical_events",
    "modify_past_outputs",
    "silently_correct_data",
    "share_mutable_state_with_other_engines",
];
/** Canonical engine registry — contracts for core continuity engines. */
exports.REGISTERED_ENGINE_CONTRACTS = [
    {
        name: "care_event_engine",
        execution_type: "deterministic",
        timing: "sync",
        mutation_authority: exports.MUTATION_AUTHORITY,
        required_inputs: ["raw_input", "caregiver_id"],
        optional_inputs: ["documents", "timestamp"],
        outputs: ["care_events", "uncertainty_labels"],
    },
    {
        name: "timeline_reconstruction_engine",
        execution_type: "deterministic",
        timing: "sync",
        mutation_authority: exports.MUTATION_AUTHORITY,
        required_inputs: ["care_events"],
        optional_inputs: ["raw_input"],
        outputs: ["timeline_nodes", "ordering_confidence"],
    },
    {
        name: "contradiction_detection_engine",
        execution_type: "deterministic",
        timing: "sync",
        mutation_authority: exports.MUTATION_AUTHORITY,
        required_inputs: ["care_events"],
        optional_inputs: ["care_timeline"],
        outputs: ["contradiction_flags", "clarification_triggers"],
    },
    {
        name: "care_context_diff_engine",
        execution_type: "deterministic",
        timing: "sync",
        mutation_authority: exports.MUTATION_AUTHORITY,
        required_inputs: ["prior_context", "current_context"],
        optional_inputs: ["events_created"],
        outputs: ["diff_sections", "primary_change"],
    },
    {
        name: "prioritization_engine",
        execution_type: "deterministic",
        timing: "sync",
        mutation_authority: exports.MUTATION_AUTHORITY,
        required_inputs: ["care_events"],
        optional_inputs: ["attention_signals"],
        outputs: ["priority_rankings", "attention_event_ids"],
    },
    {
        name: "clarification_engine",
        execution_type: "deterministic",
        timing: "sync",
        mutation_authority: exports.MUTATION_AUTHORITY,
        required_inputs: ["what_is_uncertain"],
        optional_inputs: ["raw_input"],
        outputs: ["clarification_questions"],
    },
    {
        name: "trust_layer_engine",
        execution_type: "deterministic",
        timing: "sync",
        mutation_authority: exports.MUTATION_AUTHORITY,
        required_inputs: ["care_events", "what_is_uncertain"],
        optional_inputs: ["behavior", "continuity_decay"],
        outputs: ["trust_layer_block"],
    },
    {
        name: "behavior_interpretation_engine",
        execution_type: "probabilistic",
        timing: "sync",
        mutation_authority: exports.MUTATION_AUTHORITY,
        required_inputs: ["care_events"],
        optional_inputs: ["situation_snippets"],
        outputs: ["hypotheses", "confidence_scores", "investigation_checklist"],
    },
    {
        name: "pattern_aggregation_layer",
        execution_type: "probabilistic",
        timing: "async",
        mutation_authority: exports.MUTATION_AUTHORITY,
        required_inputs: ["deidentified_feature_vectors"],
        optional_inputs: [],
        outputs: ["pattern_clusters", "transition_probabilities"],
    },
];
