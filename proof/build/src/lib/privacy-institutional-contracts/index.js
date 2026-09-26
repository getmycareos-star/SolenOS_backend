"use strict";
/**
 * Privacy + Institutional readiness — architectural contracts on CareEvents.
 * Families first. Institutions = future projections of the SAME CareContext.
 * DO NOT fork CareContext for institutions.
 */
Object.defineProperty(exports, "__esModule", { value: true });
exports.INSTITUTIONAL_READINESS_RULES = exports.PRIVACY_ARCHITECTURE_RULES = exports.SENSITIVITY_LEVELS = exports.ACTOR_ROLES = void 0;
exports.defaultPrivacyMeta = defaultPrivacyMeta;
exports.filterEventsForRole = filterEventsForRole;
exports.ACTOR_ROLES = [
    "primary_caregiver",
    "secondary_caregiver",
    "professional_caregiver",
    "clinician",
    "institutional_observer",
];
exports.SENSITIVITY_LEVELS = ["low", "medium", "high"];
exports.PRIVACY_ARCHITECTURE_RULES = [
    "data_minimization_for_care_context_only",
    "no_engine_bypass_of_privacy_gateway",
    "raw_input_highest_restriction",
    "roles_do_not_change_care_facts",
    "care_context_never_forked_for_institutions",
    "encryption_baseline_assumption",
    "export_and_erasure_user_controlled",
    "no_cross_family_raw_data",
    "hipaa_not_claimed_without_legal_technical_met",
];
exports.INSTITUTIONAL_READINESS_RULES = [
    "single_core_care_event_model",
    "institutions_are_projection_layers_only",
    "roles_are_metadata_not_structure",
    "events_must_be_exportable_and_self_contained",
    "no_hospital_mode_fork",
    "no_parallel_ingestion_pipelines",
];
function defaultPrivacyMeta(input) {
    return {
        visibility_roles: ["primary_caregiver", "secondary_caregiver"],
        ownership_scope: input.ownership_scope,
        sensitivity_level: input.is_document ? "high" : "medium",
        purpose_tags: ["care_continuity"],
        consent_present: input.consent_present ?? true,
    };
}
/** FUTURE: filter events for role — does not mutate underlying store. */
function filterEventsForRole(events, role) {
    return events.filter((e) => {
        if (!e.privacy)
            return role === "primary_caregiver";
        return e.privacy.visibility_roles.includes(role);
    });
}
