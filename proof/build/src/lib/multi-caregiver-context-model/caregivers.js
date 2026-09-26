"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.upsertCaregiver = upsertCaregiver;
exports.buildSourceConfidenceProfiles = buildSourceConfidenceProfiles;
function upsertCaregiver(caregivers, input) {
    const existing = caregivers.find((c) => c.caregiver_id === input.caregiver_id);
    if (existing) {
        return caregivers.map((c) => c.caregiver_id === input.caregiver_id
            ? {
                ...c,
                name: input.name ?? c.name,
                relationship_to_care_recipient: input.relationship_to_care_recipient ?? c.relationship_to_care_recipient,
                role: input.role ?? c.role,
                reliability_profile: {
                    ...c.reliability_profile,
                    observation_count: c.reliability_profile.observation_count + 1,
                    last_contribution_at: input.as_of,
                },
            }
            : c);
    }
    return [
        ...caregivers,
        {
            caregiver_id: input.caregiver_id,
            name: input.name ?? null,
            relationship_to_care_recipient: input.relationship_to_care_recipient ?? null,
            role: input.role ?? "family",
            contact_info: null,
            reliability_profile: {
                reliability_score: 50,
                observation_count: 1,
                last_contribution_at: input.as_of,
            },
        },
    ];
}
function buildSourceConfidenceProfiles(caregivers, events) {
    const profiles = [];
    for (const caregiver of caregivers) {
        const count = events.filter((e) => e.source_attribution?.caregiver_id === caregiver.caregiver_id).length;
        const recencyBoost = caregiver.reliability_profile.last_contribution_at ? 0.1 : 0;
        const roleBoost = caregiver.role === "medical" || caregiver.role === "professional" ? 0.15 : 0.05;
        const repeatBoost = Math.min(0.2, count * 0.03);
        profiles.push({
            caregiver_id: caregiver.caregiver_id,
            domain: "general_observation",
            confidence_weight: Math.min(0.95, 0.4 + roleBoost + repeatBoost + recencyBoost),
            basis: caregiver.role === "medical" || caregiver.role === "professional"
                ? "Role-weighted — clinical/professional observation context"
                : "Repeated caregiver observation — family/informal perspective preserved",
        });
    }
    return profiles;
}
