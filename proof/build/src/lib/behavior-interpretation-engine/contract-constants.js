"use strict";
/** Behavior Interpretation Engine — domain intelligence on CareEvents, not raw text. */
Object.defineProperty(exports, "__esModule", { value: true });
exports.REASONING_PIPELINE_STAGES = exports.UNMET_NEED_CANDIDATES = exports.INVESTIGATION_DOMAINS = exports.BEHAVIOR_TAXONOMY_GROUPS = exports.CONFIDENCE_LEVELS = exports.BEHAVIOR_PROHIBITED = exports.BEHAVIOR_ENGINE_BOUNDARY = exports.BEHAVIOR_INTERPRETATION_IDENTITY = void 0;
exports.BEHAVIOR_INTERPRETATION_IDENTITY = "SolenOS does not understand dementia — it understands care situations that happen in dementia caregiving.";
exports.BEHAVIOR_ENGINE_BOUNDARY = "Transform observable dementia-related behaviors into structured understanding — never diagnose, stage, or predict disease progression.";
exports.BEHAVIOR_PROHIBITED = [
    "infer medical condition severity",
    "label dementia stage from behavior",
    "predict disease progression",
    "give medical advice",
    "interpret symptoms clinically",
    "assume causality from disease worsening",
    "single-explanation conclusions",
    "hidden reasoning",
];
exports.CONFIDENCE_LEVELS = ["high", "medium", "low"];
exports.BEHAVIOR_TAXONOMY_GROUPS = [
    "personal_care",
    "medication",
    "emotional_distress",
    "orientation",
    "sleep",
    "eating_drinking",
    "withdrawal",
    "communication",
    "safety_incident",
    "coordination",
];
exports.INVESTIGATION_DOMAINS = ["physical", "environmental", "emotional"];
exports.UNMET_NEED_CANDIDATES = [
    "reassurance",
    "familiarity",
    "dignity",
    "safety",
    "pain_relief",
    "hydration",
    "companionship",
    "stimulation",
    "reduced_stimulation",
    "autonomy",
    "rest",
    "orientation_support",
];
exports.REASONING_PIPELINE_STAGES = [
    "observed_behavior",
    "behavior_classification",
    "possible_interpretations",
    "possible_unmet_needs",
    "investigation_checklist",
    "recommended_approach",
    "escalation_assessment",
    "context_update",
    "longitudinal_patterns",
];
