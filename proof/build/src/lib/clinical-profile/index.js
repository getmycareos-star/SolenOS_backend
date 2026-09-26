"use strict";
/**

 * Clinical Profile — SolenOS entry market vs product identity.

 *

 * Dementia is the **first clinical profile** (MVP go-to-market).

 * The product is Care Reality Intelligence / Living Care Record — not a dementia app.

 * Future profiles (Parkinson's, stroke, …) plug into the same engines without rewriting the spine.

 *

 * ADR: docs/15-architecture-decisions/ADR-005-dementia-entry-market.md

 * Docs: docs/architecture/CLINICAL_PROFILE.md

 */
Object.defineProperty(exports, "__esModule", { value: true });
exports.CAREGIVER_FACING_CLINICAL_COPY = exports.CLINICAL_PROFILE_FORBIDDEN = exports.CLINICAL_PROFILE_RULES = exports.KNOWN_CLINICAL_PROFILE_IDS = exports.DEFAULT_CLINICAL_PROFILE_ID = exports.CLINICAL_PROFILE_PURPOSE = void 0;
exports.resolveClinicalProfileId = resolveClinicalProfileId;
exports.isKnownClinicalProfileId = isKnownClinicalProfileId;
exports.resolveClinicalProfileFromCareContext = resolveClinicalProfileFromCareContext;
exports.CLINICAL_PROFILE_PURPOSE = "Provide person-condition context for unknowns and care-context extensions — never caregiver-facing disease FAQ or diagnosis.";
/** MVP / default clinical profile id. */
exports.DEFAULT_CLINICAL_PROFILE_ID = "dementia";
/**

 * Registered clinical profiles.

 * Add new ids here + a profile module under unknowns-engine/profiles — do not fork the Living Care Record.

 */
exports.KNOWN_CLINICAL_PROFILE_IDS = ["dementia"];
exports.CLINICAL_PROFILE_RULES = [
    "dementia_is_entry_market_not_product_identity",
    "default_profile_is_dementia_until_care_recipient_profile_says_otherwise",
    "engines_are_disease_agnostic_profiles_are_data",
    "caregiver_ui_never_leads_with_generic_dementia_education",
    "no_dementia_diagnosis_from_observations",
    "future_profiles_require_adr_and_registry_entry_not_spine_rewrite",
    "person_baseline_beats_condition_stereotype",
    "care_context_maps_to_clinical_profile_id",
];
exports.CLINICAL_PROFILE_FORBIDDEN = [
    "dementia_faq_chatbot",
    "why_does_dementia_cause_x",
    "symptom_encyclopedia_as_mvp",
    "hardcoding_dementia_copy_in_caregiver_composer",
    "locking_architecture_to_one_disease",
];
/** Resolve a profile id for pipelines — empty falls back to dementia MVP. */
function resolveClinicalProfileId(profileId) {
    const trimmed = profileId?.trim();
    if (!trimmed)
        return exports.DEFAULT_CLINICAL_PROFILE_ID;
    return trimmed;
}
function isKnownClinicalProfileId(id) {
    return exports.KNOWN_CLINICAL_PROFILE_IDS.includes(id);
}
/**

 * Map care-recipient `care_context` → clinical profile for Unknowns / continuity.

 * - dementia → dementia profile

 * - general → dementia default (MVP entry market; person baseline still wins in comparison)

 * - future_condition → dementia default until that profile is registered (no crash)

 */
function resolveClinicalProfileFromCareContext(careContext) {
    if (careContext === "dementia")
        return "dementia";
    if (careContext === "future_condition") {
        // Reserved: when a non-dementia profile is registered, map here via ADR.
        return exports.DEFAULT_CLINICAL_PROFILE_ID;
    }
    return exports.DEFAULT_CLINICAL_PROFILE_ID;
}
/**

 * Caregiver-facing language: never name the disease as the product.

 * Internal ops/devtools may show profile labels; Living Care Record does not.

 */
exports.CAREGIVER_FACING_CLINICAL_COPY = {
    product_name: "SolenOS",
    record_name: "Living Care Record",
    prefer: ["what changed for them", "what we know so far", "today's care situation"],
    avoid: [
        "this is common in dementia",
        "people with Alzheimer's often",
        "stage N dementia",
        "dementia tip of the day",
    ],
};
