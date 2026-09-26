"use strict";
/**
 * Care recipient display identity (MVP).
 * Product SoT: docs/02-product/solenos-mvp-identity-naming.md
 * Ask once → persist display_name → use in caregiver copy. Never silent medical identity.
 */
Object.defineProperty(exports, "__esModule", { value: true });
exports.getCareRecipientIdentity = getCareRecipientIdentity;
exports.getCareRecipientDisplayName = getCareRecipientDisplayName;
exports.setCareRecipientDisplayName = setCareRecipientDisplayName;
exports.resolveSubjectLabel = resolveSubjectLabel;
exports.resetCareRecipientIdentityStore = resetCareRecipientIdentityStore;
const fs_store_1 = require("../living-care-record-persistence/fs-store");
const memory = new Map();
function filePath(careKey) {
    return (0, fs_store_1.livingCareRecordDataDir)("care-recipient-identity", `${(0, fs_store_1.sanitizeDurableCareKey)(careKey)}.json`);
}
function getCareRecipientIdentity(careKey) {
    const cached = memory.get(careKey);
    if (cached)
        return cached;
    const durable = (0, fs_store_1.readDurableJson)(filePath(careKey));
    if (durable?.display_name?.trim()) {
        memory.set(careKey, durable);
        return durable;
    }
    return null;
}
function getCareRecipientDisplayName(careKey) {
    const id = getCareRecipientIdentity(careKey);
    const name = id?.display_name?.trim();
    return name || null;
}
/** Ask-once set / rename later. Display label only — not medical identity. */
function setCareRecipientDisplayName(params) {
    const display_name = params.displayName.trim().slice(0, 80);
    if (!display_name) {
        throw new Error("Display name is required");
    }
    // Avoid case-file language as the chosen name.
    const banned = /^(the patient|the subject|the individual|your loved one)$/i;
    if (banned.test(display_name)) {
        throw new Error("Choose a personal name (e.g. Mom, Dad, or a given name)");
    }
    const record = {
        care_key: params.careKey,
        display_name,
        relationship: params.relationship ?? null,
        updated_at: new Date().toISOString(),
    };
    memory.set(params.careKey, record);
    (0, fs_store_1.writeDurableJson)(filePath(params.careKey), record);
    return record;
}
/**
 * Prefer durable display_name from ask-once identity.
 * Never silently infer Mom/Dad/given names from notes into identity (locked A).
 * Neutral fallback — never “Your loved one” (identity naming Locked A).
 */
function resolveSubjectLabel(params) {
    const display = getCareRecipientDisplayName(params.careKey);
    if (display)
        return display;
    void params.rawText;
    return "they";
}
function resetCareRecipientIdentityStore() {
    memory.clear();
    (0, fs_store_1.clearDurableDirectory)((0, fs_store_1.livingCareRecordDataDir)("care-recipient-identity"));
}
