"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.selectBehaviorProfile = selectBehaviorProfile;
exports.formatBehaviorConstraint = formatBehaviorConstraint;
const PROFILES = {
    crisis_urgent: {
        verbosity_factor: 0.85,
        escalation_sensitivity: "maximum",
        uncertainty_strictness: "strict",
        prioritization_aggressiveness: "elevated",
        emotional_acknowledgment: "minimal",
    },
    medical_document: {
        verbosity_factor: 0.95,
        escalation_sensitivity: "low",
        uncertainty_strictness: "strict",
        prioritization_aggressiveness: "standard",
        emotional_acknowledgment: "minimal",
    },
    emotional_narrative: {
        verbosity_factor: 1,
        escalation_sensitivity: "standard",
        uncertainty_strictness: "standard",
        prioritization_aggressiveness: "standard",
        emotional_acknowledgment: "elevated",
    },
    administrative_legal: {
        verbosity_factor: 1,
        escalation_sensitivity: "low",
        uncertainty_strictness: "standard",
        prioritization_aggressiveness: "standard",
        emotional_acknowledgment: "minimal",
    },
};
function selectBehaviorProfile(classification) {
    return { mode: classification.mode, ...PROFILES[classification.mode] };
}
function formatBehaviorConstraint(profile) {
    return [
        `INPUT_MODE: ${profile.mode}`,
        `ESCALATION_SENSITIVITY: ${profile.escalation_sensitivity}`,
        `UNCERTAINTY_STRICTNESS: ${profile.uncertainty_strictness}`,
        `PRIORITIZATION: ${profile.prioritization_aggressiveness}`,
        `EMOTIONAL_ACK: ${profile.emotional_acknowledgment}`,
    ].join(" | ");
}
