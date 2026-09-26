"use strict";
/**
 * Durable Care Reality State — Map cache + `.data/care-reality-state/`.
 * Uses shared isomorphic fs helpers (no-ops in the browser).
 */
Object.defineProperty(exports, "__esModule", { value: true });
exports.crsCache = crsCache;
exports.loadCareRealityStateFromDurable = loadCareRealityStateFromDurable;
exports.persistCareRealityStateToDurable = persistCareRealityStateToDurable;
exports.deleteCareRealityStateDurable = deleteCareRealityStateDurable;
exports.clearCareRealityStateMemoryCache = clearCareRealityStateMemoryCache;
exports.resetCareRealityStateDurableStore = resetCareRealityStateDurableStore;
const fs_store_1 = require("../living-care-record-persistence/fs-store");
const memory = new Map();
function filePath(caregiverId) {
    return (0, fs_store_1.livingCareRecordDataDir)("care-reality-state", `${(0, fs_store_1.sanitizeDurableCareKey)(caregiverId)}.json`);
}
function crsCache() {
    return memory;
}
function loadCareRealityStateFromDurable(caregiverId) {
    return (0, fs_store_1.readDurableJson)(filePath(caregiverId));
}
function persistCareRealityStateToDurable(state) {
    const key = state.care_recipient_id ?? state.caregiver_id;
    (0, fs_store_1.writeDurableJson)(filePath(key), state);
}
function deleteCareRealityStateDurable(caregiverId) {
    (0, fs_store_1.deleteDurableFile)(filePath(caregiverId));
}
function clearCareRealityStateMemoryCache() {
    memory.clear();
}
function resetCareRealityStateDurableStore() {
    memory.clear();
    (0, fs_store_1.clearDurableDirectory)((0, fs_store_1.livingCareRecordDataDir)("care-reality-state"));
}
