"use strict";
/** Input Classification Control System — surface-signal routing only. */
Object.defineProperty(exports, "__esModule", { value: true });
exports.INPUT_CLASSIFICATION_FAILURE_MODEL = exports.LOW_CONFIDENCE_THRESHOLD = exports.LOW_CONFIDENCE_DEFAULT_MODE = exports.INPUT_CLASSIFICATION_FORBIDDEN_EFFECTS = exports.INPUT_CLASSIFICATION_ALLOWED_EFFECTS = exports.INPUT_CLASSIFICATION_FORBIDDEN = exports.INPUT_MODES = exports.INPUT_CLASSIFICATION_PIPELINE = exports.INPUT_CLASSIFICATION_ONE_LINE_TRUTH = exports.INPUT_CLASSIFICATION_IDENTITY = void 0;
exports.INPUT_CLASSIFICATION_IDENTITY = "a behavioral control system, not an interpretation or reasoning system";
exports.INPUT_CLASSIFICATION_ONE_LINE_TRUTH = "In SolenOS, classification is not understanding — it is constraint selection for a deterministic cognitive decompression engine.";
exports.INPUT_CLASSIFICATION_PIPELINE = [
    "INPUT RECEIVED",
    "INPUT CLASSIFICATION",
    "BEHAVIOR PROFILE SELECTION",
    "SAFETY CONSTRAINT APPLICATION",
    "STRUCTURED OUTPUT GENERATION",
];
exports.INPUT_MODES = [
    "medical_document",
    "emotional_narrative",
    "administrative_legal",
    "crisis_urgent",
];
exports.INPUT_CLASSIFICATION_FORBIDDEN = [
    "intelligence layer",
    "reasoning engine",
    "diagnostic system",
    "sentiment analyzer",
    "personalization model",
    "user profiling tool",
    "behavioral predictor",
];
exports.INPUT_CLASSIFICATION_ALLOWED_EFFECTS = [
    "verbosity limits",
    "escalation sensitivity",
    "uncertainty strictness",
    "prioritization aggressiveness",
    "emotional acknowledgment intensity",
];
exports.INPUT_CLASSIFICATION_FORBIDDEN_EFFECTS = [
    "change output schema",
    "change semantic structure",
    "change section meanings",
    "introduce new logic paths",
];
exports.LOW_CONFIDENCE_DEFAULT_MODE = "emotional_narrative";
exports.LOW_CONFIDENCE_THRESHOLD = 0.55;
exports.INPUT_CLASSIFICATION_FAILURE_MODEL = "SolenOS fails when the classifier infers diagnosis, emotional state beyond explicit text, hidden intent, medical conditions, urgency from ambiguity, or constructs narratives.";
