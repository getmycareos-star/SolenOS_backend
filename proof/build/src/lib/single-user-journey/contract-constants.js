"use strict";
/** Single User Journey — end-to-end MVP continuity loop (first 10 minutes). */
Object.defineProperty(exports, "__esModule", { value: true });
exports.EXAMPLE_SECOND_INPUT = exports.EXAMPLE_FIRST_INPUT = exports.JOURNEY_RULES = exports.JOURNEY_PROHIBITED = exports.JOURNEY_OUTPUT_MODES_ALLOWED = exports.JOURNEY_STEPS = exports.JOURNEY_MVP_DEFINITION = exports.SINGLE_USER_JOURNEY_DEFINING_PRINCIPLE = exports.SINGLE_USER_JOURNEY_IDENTITY = void 0;
exports.SINGLE_USER_JOURNEY_IDENTITY = "SolenOS is only valid if the first interaction produces a complete working continuity loop.";
exports.SINGLE_USER_JOURNEY_DEFINING_PRINCIPLE = "SolenOS is not a sequence of interactions. It is a continuously evolving state machine over care reality.";
exports.JOURNEY_MVP_DEFINITION = "A user can submit 2 messy inputs and see continuity emerge (state + change over time).";
/** Exact runtime sequence — no step may be skipped. */
exports.JOURNEY_STEPS = [
    "system_entry",
    "input_classification",
    "bootstrap_care_context",
    "first_care_event_creation",
    "engine_execution_first_pass",
    "first_state_of_care_output",
    "first_user_value_moment",
    "return_loop_continuation_input",
    "context_retrieval",
    "difference_computation",
    "care_context_projection_update",
    "state_output_change_over_time",
    "continuity_confirmation",
];
exports.JOURNEY_OUTPUT_MODES_ALLOWED = [
    "state_of_care_summary",
    "diff_output",
    "clarification",
    "crisis_output",
    "first_60s_value_loop",
    "return_value_loop",
];
exports.JOURNEY_PROHIBITED = [
    "greeting_the_user",
    "vague_conversational_questions",
    "chatbot_assistant_behavior",
    "onboarding_wizard",
    "setup_forms_before_value",
    "skipping_care_event_creation",
    "skipping_diff_on_second_input",
    "resetting_care_context_between_inputs",
];
exports.JOURNEY_RULES = [
    "no_step_skipped",
    "no_chat_behavior",
    "output_always_state_driven",
    "first_value_under_ten_seconds_logical",
    "care_context_persistent",
    "event_sourcing_at_every_step",
    "two_inputs_prove_continuity",
];
exports.EXAMPLE_FIRST_INPUT = "Dad seems confused and almost fell yesterday";
exports.EXAMPLE_SECOND_INPUT = "Actually he fell again this morning and is worse";
