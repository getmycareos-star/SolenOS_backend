"use strict";
/**
 * MVP Input Architecture — text + documents only.
 * Voice enters the same pipeline later; not an MVP product surface.
 * ADR: docs/15-architecture-decisions/ADR-018-mvp-input-text-documents-only.md
 */
Object.defineProperty(exports, "__esModule", { value: true });
exports.toCaregiverFacingLine = exports.sanitizeCaregiverErrorMessage = exports.sanitizeCaregiverDisplayText = exports.resolveCaregiverWords = exports.isRetrospectiveCareReport = exports.isImmediateDangerLanguage = exports.isGenericSignalText = exports.isCaregiverSafeDisplayText = exports.isBareSchemaField = exports.INPUT_MESSINESS_LEVELS = exports.humanizeUncertaintyForCaregiver = exports.FIRST_INPUT_INVARIANTS = exports.dedupeCaregiverFacingLines = exports.classifyInputMessiness = exports.CAREGIVER_FORBIDDEN_UI_TERMS = exports.MVP_INPUT_PROOF_QUESTION = exports.MVP_INPUT_FORBIDDEN_SURFACES = exports.MVP_INPUT_FLOW = exports.MVP_INPUT_PRINCIPLES = exports.FUTURE_INPUT_CHANNELS = exports.MVP_INPUT_CHANNELS = exports.MVP_INPUT_ARCHITECTURE_IDENTITY = void 0;
exports.isMvpInputChannel = isMvpInputChannel;
exports.isForbiddenMvpVoiceSurface = isForbiddenMvpVoiceSurface;
exports.MVP_INPUT_ARCHITECTURE_IDENTITY = "SolenOS MVP accepts messy text and documents — not voice.";
exports.MVP_INPUT_CHANNELS = ["text", "document"];
/** Future channel — same User Input → Understanding → Care Record path. */
exports.FUTURE_INPUT_CHANNELS = ["voice"];
exports.MVP_INPUT_PRINCIPLES = [
    "accept_messy_incomplete_unstructured_input",
    "first_input_content_unknown_messiness_predictable",
    "no_perfect_structure_required_before_value",
    "documents_and_text_only_in_mvp_ui",
    "voice_is_future_input_not_mvp",
    "pipeline_stays_generic_for_future_channels",
];
exports.MVP_INPUT_FLOW = [
    "document_or_text",
    "processing_layer",
    "understanding_extraction",
    "care_actions_and_timeline",
];
exports.MVP_INPUT_FORBIDDEN_SURFACES = [
    "voice_input_mic",
    "speech_recognition_ui",
    "voice_conversation_mode",
    "text_to_speech_hear_solenos",
    "whisper_or_voice_api_product_path",
];
exports.MVP_INPUT_PROOF_QUESTION = "Can SolenOS turn scattered caregiver information into understandable next steps?";
function isMvpInputChannel(channel) {
    return exports.MVP_INPUT_CHANNELS.includes(channel);
}
/** True when a UI/feature description is a forbidden MVP voice surface. */
function isForbiddenMvpVoiceSurface(description) {
    const lower = description.toLowerCase();
    return [
        /\bmic(rophone)?\b/,
        /\bvoice input\b/,
        /\bspeech recognition\b/,
        /\bvoice conversation\b/,
        /\btext[- ]to[- ]speech\b/,
        /\bhear solenos\b/,
        /\bread aloud\b/,
        /\bwhisper\b/,
        /\btts\b/,
    ].some((p) => p.test(lower));
}
var first_input_1 = require("./first-input");
Object.defineProperty(exports, "CAREGIVER_FORBIDDEN_UI_TERMS", { enumerable: true, get: function () { return first_input_1.CAREGIVER_FORBIDDEN_UI_TERMS; } });
Object.defineProperty(exports, "classifyInputMessiness", { enumerable: true, get: function () { return first_input_1.classifyInputMessiness; } });
Object.defineProperty(exports, "dedupeCaregiverFacingLines", { enumerable: true, get: function () { return first_input_1.dedupeCaregiverFacingLines; } });
Object.defineProperty(exports, "FIRST_INPUT_INVARIANTS", { enumerable: true, get: function () { return first_input_1.FIRST_INPUT_INVARIANTS; } });
Object.defineProperty(exports, "humanizeUncertaintyForCaregiver", { enumerable: true, get: function () { return first_input_1.humanizeUncertaintyForCaregiver; } });
Object.defineProperty(exports, "INPUT_MESSINESS_LEVELS", { enumerable: true, get: function () { return first_input_1.INPUT_MESSINESS_LEVELS; } });
Object.defineProperty(exports, "isBareSchemaField", { enumerable: true, get: function () { return first_input_1.isBareSchemaField; } });
Object.defineProperty(exports, "isCaregiverSafeDisplayText", { enumerable: true, get: function () { return first_input_1.isCaregiverSafeDisplayText; } });
Object.defineProperty(exports, "isGenericSignalText", { enumerable: true, get: function () { return first_input_1.isGenericSignalText; } });
Object.defineProperty(exports, "isImmediateDangerLanguage", { enumerable: true, get: function () { return first_input_1.isImmediateDangerLanguage; } });
Object.defineProperty(exports, "isRetrospectiveCareReport", { enumerable: true, get: function () { return first_input_1.isRetrospectiveCareReport; } });
Object.defineProperty(exports, "resolveCaregiverWords", { enumerable: true, get: function () { return first_input_1.resolveCaregiverWords; } });
Object.defineProperty(exports, "sanitizeCaregiverDisplayText", { enumerable: true, get: function () { return first_input_1.sanitizeCaregiverDisplayText; } });
Object.defineProperty(exports, "sanitizeCaregiverErrorMessage", { enumerable: true, get: function () { return first_input_1.sanitizeCaregiverErrorMessage; } });
Object.defineProperty(exports, "toCaregiverFacingLine", { enumerable: true, get: function () { return first_input_1.toCaregiverFacingLine; } });
