"use strict";
/** MVP Research Validation — product constraints from caregiver research. */
Object.defineProperty(exports, "__esModule", { value: true });
exports.RESEARCH_ENGINEERING_PRIORITY = exports.RESEARCH_DO_NOT_BUILD_NOW = exports.RESEARCH_BUILD_NOW = exports.RESEARCH_MVP_MUST_CREATE = exports.RESEARCH_SUCCESS_FEEL = exports.RESEARCH_RETENTION_HYPOTHESIS = exports.RESEARCH_VALIDATION_PURPOSE = void 0;
exports.RESEARCH_VALIDATION_PURPOSE = "Prove SolenOS reduces uncertainty and mental load as an external memory for care reality — not a productivity tool.";
/** Four questions the MVP must be able to answer affirmatively. */
exports.RESEARCH_RETENTION_HYPOTHESIS = [
    "understand_what_is_happening_better",
    "less_afraid_of_forgetting_something_important",
    "can_explain_the_situation_better_to_another_person",
    "would_use_again_when_something_changes",
];
exports.RESEARCH_SUCCESS_FEEL = {
    before: "I have so much information but I don't understand what is happening.",
    after: "I understand what changed, what matters now, what decisions happened, and what is still uncertain.",
    put_down: "I can put this somewhere and trust it will make sense later.",
    never: "I have another system to maintain.",
};
/** Every capture should create these understanding facets. */
exports.RESEARCH_MVP_MUST_CREATE = [
    "what_happened",
    "what_changed",
    "what_is_connected",
    "what_is_unknown",
];
exports.RESEARCH_BUILD_NOW = [
    "care_reality_timeline",
    "situation_relationships",
    "decision_memory",
    "evidence_linking",
    "source_attribution",
    "change_detection",
    "unknown_preservation",
    "document_to_care_reality",
];
exports.RESEARCH_DO_NOT_BUILD_NOW = [
    "healthcare_navigation_marketplace",
    "training_platform",
    "financial_assistance_platform",
    "medical_advice_engine",
    "care_coordinator_replacement",
    "large_resource_database",
];
exports.RESEARCH_ENGINEERING_PRIORITY = [
    "care_reality_persistence",
    "situation_relationship_engine",
    "decision_memory",
    "evidence_linking",
    "current_state_understanding",
    "document_to_care_reality_pipeline",
    "response_behavior",
    "mobile_simplicity",
];
