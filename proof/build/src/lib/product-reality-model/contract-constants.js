"use strict";
/** Product Reality Model — operating assumptions for fragmented, contradictory care environments. */
Object.defineProperty(exports, "__esModule", { value: true });
exports.FAILURE_MODES = exports.REALITY_MODEL_RULES = exports.CORRECT_MODEL_RULES = exports.WRONG_MODEL_PROHIBITIONS = exports.OPERATING_ASSUMPTIONS = exports.PRODUCT_REALITY_DEFINING_PRINCIPLE = exports.PRODUCT_REALITY_MODEL_IDENTITY = void 0;
exports.PRODUCT_REALITY_MODEL_IDENTITY = "SolenOS is a contradiction-tolerant, event-driven coordination system for fragmented caregiver environments.";
exports.PRODUCT_REALITY_DEFINING_PRINCIPLE = "Build for exhaustion, randomness, contradiction, and incomplete information as default conditions.";
exports.OPERATING_ASSUMPTIONS = [
    "users_are_exhausted",
    "input_is_random",
    "contradiction_is_normal",
    "state_is_incomplete",
];
exports.WRONG_MODEL_PROHIBITIONS = [
    "clean_user_profiles_as_primary",
    "structured_onboarding_required",
    "complete_datasets_expected",
    "deterministic_healthcare_records",
    "manual_state_authoring",
    "silent_conflict_overwrite",
];
exports.CORRECT_MODEL_RULES = [
    "event_first_not_form_first",
    "state_is_derived",
    "conflict_is_first_class",
    "missing_data_explicit",
    "probabilistic_care_state",
];
exports.REALITY_MODEL_RULES = [
    ...exports.OPERATING_ASSUMPTIONS,
    ...exports.CORRECT_MODEL_RULES,
];
exports.FAILURE_MODES = [
    "assumes_clean_input",
    "hides_contradictions",
    "forces_structured_onboarding",
    "over_summarizes_into_false_certainty",
    "deletes_conflicting_data",
];
