"use strict";
/**
 * Durable Active Care Situation — source of truth under `.data/active-care-situation/`.
 * In-memory Map is a cache only.
 */
Object.defineProperty(exports, "__esModule", { value: true });
exports.acsCache = acsCache;
exports.acsDurablePath = acsDurablePath;
exports.loadActiveCareSituationFromDurable = loadActiveCareSituationFromDurable;
exports.persistActiveCareSituationToDurable = persistActiveCareSituationToDurable;
exports.deleteActiveCareSituationDurable = deleteActiveCareSituationDurable;
exports.clearActiveCareSituationMemoryCache = clearActiveCareSituationMemoryCache;
exports.resetActiveCareSituationDurableStore = resetActiveCareSituationDurableStore;
const fs_store_1 = require("../living-care-record-persistence/fs-store");
const GLOBAL_KEY = "__solenos_acs_cache__";
function acsCache() {
    const g = globalThis;
    if (!g[GLOBAL_KEY]) {
        g[GLOBAL_KEY] = new Map();
    }
    return g[GLOBAL_KEY];
}
function acsDurablePath(caregiverId) {
    return (0, fs_store_1.livingCareRecordDataDir)("active-care-situation", `${(0, fs_store_1.sanitizeDurableCareKey)(caregiverId)}.json`);
}
function loadActiveCareSituationFromDurable(caregiverId) {
    return (0, fs_store_1.readDurableJson)(acsDurablePath(caregiverId));
}
function persistActiveCareSituationToDurable(situation) {
    const key = situation.care_recipient_id ?? situation.caregiver_id;
    (0, fs_store_1.writeDurableJson)(acsDurablePath(key), situation);
}
function deleteActiveCareSituationDurable(caregiverId) {
    (0, fs_store_1.deleteDurableFile)(acsDurablePath(caregiverId));
}
/** Drop Map cache only — durable files remain (simulates process bounce). */
function clearActiveCareSituationMemoryCache() {
    acsCache().clear();
}
/** Clear cache + durable ACS files (verify / empty reset). */
function resetActiveCareSituationDurableStore() {
    acsCache().clear();
    (0, fs_store_1.clearDurableDirectory)((0, fs_store_1.livingCareRecordDataDir)("active-care-situation"));
}
