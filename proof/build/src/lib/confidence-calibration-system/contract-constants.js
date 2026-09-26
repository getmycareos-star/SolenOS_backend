"use strict";
/** Confidence Calibration System — computed probability of correctness, not a static label. */
Object.defineProperty(exports, "__esModule", { value: true });
exports.CONFIDENCE_CALIBRATION_RULES = exports.COMPLETENESS_PENALTY_PER_MISSING = exports.CONTRADICTION_PENALTY = exports.CONFIRMATION_BOOST = exports.HIGH_RISK_DECAY_HALF_LIFE_DAYS = exports.STABLE_DECAY_HALF_LIFE_DAYS = exports.INFERENCE_CEILING = exports.CONFIDENCE_CEILING = exports.CONFIDENCE_FLOOR = exports.SOURCE_TYPE_WEIGHTS = exports.CONFIDENCE_CALIBRATION_DEFINING_PRINCIPLE = exports.CONFIDENCE_CALIBRATION_IDENTITY = void 0;
exports.CONFIDENCE_CALIBRATION_IDENTITY = "Confidence in SolenOS is a continuously evolving measurement of evidence strength under time, contradiction, and reinforcement dynamics.";
exports.CONFIDENCE_CALIBRATION_DEFINING_PRINCIPLE = "Confidence is a computed, continuously updated probability of correctness — not a static attribute.";
exports.SOURCE_TYPE_WEIGHTS = {
    medical_professional: 0.95,
    caregiver_direct_observation: 0.8,
    reported_second_hand: 0.65,
    system_inference: 0.5,
    unverified_input: 0.4,
};
exports.CONFIDENCE_FLOOR = 0.15;
exports.CONFIDENCE_CEILING = 0.95;
/** Inferred data cannot exceed this relative to observed ceiling */
exports.INFERENCE_CEILING = 0.75;
/** Decay half-life in days for stable conditions */
exports.STABLE_DECAY_HALF_LIFE_DAYS = 30;
/** Faster decay for high-risk contexts */
exports.HIGH_RISK_DECAY_HALF_LIFE_DAYS = 7;
exports.CONFIRMATION_BOOST = 0.08;
exports.CONTRADICTION_PENALTY = 0.18;
exports.COMPLETENESS_PENALTY_PER_MISSING = 0.06;
exports.CONFIDENCE_CALIBRATION_RULES = [
    "deterministic_scoring_function",
    "observations_dominate_inference",
    "recency_decay_required",
    "confirmation_boosts_stability",
    "contradiction_reduces_certainty",
    "incomplete_context_lowers_ceiling",
    "no_manual_assignment_in_production",
    "floor_and_ceiling_enforced",
];
