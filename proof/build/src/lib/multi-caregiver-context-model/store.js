"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.linkCaregiverToRecipient = linkCaregiverToRecipient;
exports.ensureContributorCareReality = ensureContributorCareReality;
exports.resolveCareRealityStoreKey = resolveCareRealityStoreKey;
exports.resolveCareRecipientId = resolveCareRecipientId;
exports.appendRecipientEvents = appendRecipientEvents;
exports.getRecipientEvents = getRecipientEvents;
exports.getRecipientContext = getRecipientContext;
exports.saveRecipientContext = saveRecipientContext;
exports.resetMultiCaregiverContextStore = resetMultiCaregiverContextStore;
const contract_constants_1 = require("./contract-constants");
const fs_store_1 = require("../living-care-record-persistence/fs-store");
const recipientContexts = new Map();
const caregiverToRecipient = new Map();
const recipientEvents = new Map();
let linksHydrated = false;
function linksPath() {
    return (0, fs_store_1.livingCareRecordDataDir)("care-reality-links", "contributor-to-recipient.json");
}
function hydrateLinks() {
    if (linksHydrated)
        return;
    linksHydrated = true;
    const durable = (0, fs_store_1.readDurableJson)(linksPath());
    if (!durable)
        return;
    for (const [contributorId, careRecipientId] of Object.entries(durable)) {
        if (contributorId && careRecipientId) {
            caregiverToRecipient.set(contributorId, careRecipientId);
        }
    }
}
function persistLinks() {
    const obj = {};
    for (const [contributorId, careRecipientId] of caregiverToRecipient) {
        obj[contributorId] = careRecipientId;
    }
    (0, fs_store_1.writeDurableJson)(linksPath(), obj);
}
/**
 * Link a contributor to a Care Reality (care recipient).
 * Locked B: many contributors → one Living Care Record.
 */
function linkCaregiverToRecipient(caregiverId, careRecipientId) {
    hydrateLinks();
    caregiverToRecipient.set(caregiverId, careRecipientId);
    persistLinks();
}
/**
 * Ensure this contributor has a Care Reality.
 * - Optional joinCareRecipientId attaches them to an existing shared reality.
 * - Otherwise mint a personal care_recipient_id (never a global shared default).
 */
function ensureContributorCareReality(contributorId, joinCareRecipientId) {
    hydrateLinks();
    const join = joinCareRecipientId?.trim();
    if (join) {
        linkCaregiverToRecipient(contributorId, join);
        return join;
    }
    const existing = caregiverToRecipient.get(contributorId);
    if (existing)
        return existing;
    const minted = `cr_${(0, fs_store_1.sanitizeDurableCareKey)(contributorId)}`;
    linkCaregiverToRecipient(contributorId, minted);
    return minted;
}
/**
 * Resolve durable store key for ACS / CRS / CareContext.
 * Accepts contributor id or an existing care_recipient_id (no double-mint).
 */
function resolveCareRealityStoreKey(contributorOrRealityId) {
    hydrateLinks();
    for (const recipientId of caregiverToRecipient.values()) {
        if (recipientId === contributorOrRealityId)
            return contributorOrRealityId;
    }
    const linked = caregiverToRecipient.get(contributorOrRealityId);
    if (linked)
        return linked;
    if (contributorOrRealityId.startsWith("cr_") ||
        contributorOrRealityId === contract_constants_1.DEFAULT_CARE_RECIPIENT_ID) {
        return contributorOrRealityId;
    }
    return ensureContributorCareReality(contributorOrRealityId);
}
/** Resolve Care Reality id for a contributor (auto-mints if unlinked). */
function resolveCareRecipientId(caregiverId) {
    return ensureContributorCareReality(caregiverId);
}
function appendRecipientEvents(careRecipientId, events) {
    const prior = recipientEvents.get(careRecipientId) ?? [];
    const byId = new Map(prior.map((e) => [e.id, e]));
    for (const e of events)
        byId.set(e.id, e);
    recipientEvents.set(careRecipientId, [...byId.values()]);
}
function getRecipientEvents(careRecipientId) {
    return recipientEvents.get(careRecipientId) ?? [];
}
function getRecipientContext(careRecipientId) {
    const existing = recipientContexts.get(careRecipientId);
    if (existing)
        return existing;
    const created = {
        care_recipient_id: careRecipientId,
        care_recipient_label: null,
        caregivers: [],
        attribution_map: [],
        source_confidence_profiles: [],
        conflict_log: [],
    };
    recipientContexts.set(careRecipientId, created);
    return created;
}
function saveRecipientContext(context) {
    recipientContexts.set(context.care_recipient_id, context);
}
function resetMultiCaregiverContextStore() {
    recipientContexts.clear();
    caregiverToRecipient.clear();
    recipientEvents.clear();
    linksHydrated = false;
}
