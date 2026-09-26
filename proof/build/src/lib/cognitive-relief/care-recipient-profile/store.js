"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.careRecipientProfileSchema = void 0;
exports.createProfileId = createProfileId;
exports.getOrCreateProfile = getOrCreateProfile;
exports.getProfileById = getProfileById;
exports.updateProfileData = updateProfileData;
exports.patchProfileRecord = patchProfileRecord;
exports.resetCareRecipientProfileStore = resetCareRecipientProfileStore;
const contract_constants_1 = require("../contract-constants");
const types_1 = require("../types");
const profiles = new Map();
const caregiverIndex = new Map();
function profileKey(caregiverId, caseId) {
    return `${caregiverId}::${caseId ?? "default"}`;
}
function createProfileId() {
    return `crp_${Date.now()}_${Math.random().toString(36).slice(2, 9)}`;
}
function getOrCreateProfile(params) {
    const caregiverId = params.caregiver_id ?? contract_constants_1.DEFAULT_CAREGIVER_ID;
    const caseId = params.case_id ?? null;
    const key = profileKey(caregiverId, caseId);
    const existingId = caregiverIndex.get(key);
    if (existingId) {
        const found = profiles.get(existingId);
        if (found)
            return found;
    }
    const now = new Date().toISOString();
    const record = {
        id: createProfileId(),
        case_id: caseId,
        caregiver_id: caregiverId,
        profile: { ...types_1.DEFAULT_PROFILE, tagged_event_log: [], location_index: [] },
        care_context: "general",
        dementia_context: null,
        last_checkin_at: null,
        checkin_period: null,
        optional_budget: null,
        created_at: now,
        updated_at: now,
    };
    profiles.set(record.id, record);
    caregiverIndex.set(key, record.id);
    return record;
}
function getProfileById(id) {
    return profiles.get(id);
}
function updateProfileData(id, updater) {
    const record = profiles.get(id);
    if (!record)
        return undefined;
    const updated = {
        ...record,
        profile: updater(record.profile),
        updated_at: new Date().toISOString(),
    };
    profiles.set(id, updated);
    return updated;
}
function patchProfileRecord(id, patch) {
    const record = profiles.get(id);
    if (!record)
        return undefined;
    const updated = { ...record, ...patch, updated_at: new Date().toISOString() };
    profiles.set(id, updated);
    return updated;
}
function resetCareRecipientProfileStore() {
    profiles.clear();
    caregiverIndex.clear();
}
exports.careRecipientProfileSchema = {
    table: "care_recipient_profiles",
    columns: [
        "id",
        "case_id",
        "caregiver_id",
        "profile",
        "last_checkin_at",
        "checkin_period",
        "optional_budget",
        "care_context",
        "dementia_context",
        "created_at",
        "updated_at",
    ],
};
