"use strict";
/** Multi-Caregiver Context Model — structural requirement for CareContext design. */
Object.defineProperty(exports, "__esModule", { value: true });
exports.URGENT_INPUT_THRESHOLD = exports.URGENT_INPUT_WINDOW_MINUTES = exports.CONTRADICTION_PATTERNS = exports.DEFAULT_CARE_RECIPIENT_ID = exports.MULTI_CAREGIVER_DESIGN_RULES = exports.MULTI_CAREGIVER_PRIVACY_RULES = exports.CONFLICT_RESOLUTION_STATUSES = exports.SOURCE_TYPES = exports.CAREGIVER_ROLES = exports.MULTI_CAREGIVER_DEFINING_PRINCIPLE = exports.MULTI_CAREGIVER_CONTEXT_IDENTITY = void 0;
exports.MULTI_CAREGIVER_CONTEXT_IDENTITY = "SolenOS constructs a single evolving CareContext from multiple private caregiver observations.";
exports.MULTI_CAREGIVER_DEFINING_PRINCIPLE = "Shared intelligence layer over reality — not a shared communication channel.";
exports.CAREGIVER_ROLES = ["family", "professional", "medical", "informal"];
exports.SOURCE_TYPES = ["direct_observation", "reported", "inferred"];
exports.CONFLICT_RESOLUTION_STATUSES = [
    "open",
    "clarification_requested",
    "preserved_both",
    "resolved",
];
exports.MULTI_CAREGIVER_PRIVACY_RULES = [
    "never_expose_raw_inputs_across_users",
    /** No chat feed / no dumping private notes across separate users. Shared Living Care Record may show perspective labels (G16). */
    "never_build_caregiver_chat_feed",
    "never_surface_private_raw_inputs_as_chat",
    "full_internal_auditability",
    "sensor_fusion_not_communication",
];
exports.MULTI_CAREGIVER_DESIGN_RULES = [
    "attribution_mandatory",
    "preserve_conflicting_perspectives",
    "never_overwrite_minority_input",
    "conflict_is_data_not_error",
    "source_weighting_future_ready",
    "no_single_user_assumptions",
    "no_anonymous_caregiver_input",
];
exports.DEFAULT_CARE_RECIPIENT_ID = "default_care_recipient";
exports.CONTRADICTION_PATTERNS = [
    {
        type: "appetite",
        less: /\b(eating less|poor appetite|not eating|decreased appetite|appetite decline)\b/i,
        more: /\b(normal appetite|eating well|good appetite|appetite stable)\b/i,
    },
    {
        type: "mobility",
        decline: /\b(uses walker|wheelchair|fallen|fell|mobility decline)\b/i,
        stable: /\b(walks independently|no falls|mobility stable|ambulat\w* well)\b/i,
    },
];
exports.URGENT_INPUT_WINDOW_MINUTES = 30;
exports.URGENT_INPUT_THRESHOLD = 2;
