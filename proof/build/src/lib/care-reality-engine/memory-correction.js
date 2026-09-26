"use strict";
/**
 * Phase 12 — Memory Correction System.
 * Never silently overwrite. Maintain history: original + correction event.
 */
Object.defineProperty(exports, "__esModule", { value: true });
exports.recordMemoryCorrection = recordMemoryCorrection;
exports.listMemoryCorrections = listMemoryCorrections;
exports.resetMemoryCorrectionStore = resetMemoryCorrectionStore;
const fs_store_1 = require("../living-care-record-persistence/fs-store");
const multi_caregiver_context_model_1 = require("../multi-caregiver-context-model");
const memory = new Map();
function pathFor(id) {
    return (0, fs_store_1.livingCareRecordDataDir)("memory-corrections", `${(0, fs_store_1.sanitizeDurableCareKey)(id)}.json`);
}
function load(id) {
    const cached = memory.get(id);
    if (cached)
        return cached;
    const durable = (0, fs_store_1.readDurableJson)(pathFor(id));
    if (durable?.corrections) {
        memory.set(id, durable);
        return durable;
    }
    return {
        care_recipient_id: id,
        corrections: [],
        updated_at: new Date().toISOString(),
    };
}
function save(store) {
    memory.set(store.care_recipient_id, store);
    (0, fs_store_1.writeDurableJson)(pathFor(store.care_recipient_id), store);
}
function recordMemoryCorrection(params) {
    const careRecipientId = (0, multi_caregiver_context_model_1.resolveCareRealityStoreKey)(params.careRecipientId);
    const now = params.nowIso ?? new Date().toISOString();
    const store = load(careRecipientId);
    const record = {
        id: `corr_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
        care_recipient_id: careRecipientId,
        field_label: params.fieldLabel.trim(),
        original_value: params.originalValue.trim(),
        corrected_value: params.correctedValue.trim(),
        corrected_by: params.correctedBy,
        corrected_at: now,
        reason: params.reason ?? null,
        history_preserved: true,
    };
    store.corrections = [...store.corrections, record].slice(-100);
    store.updated_at = now;
    save(store);
    return record;
}
function listMemoryCorrections(careRecipientId) {
    return [...load((0, multi_caregiver_context_model_1.resolveCareRealityStoreKey)(careRecipientId)).corrections];
}
function resetMemoryCorrectionStore() {
    memory.clear();
    (0, fs_store_1.clearDurableDirectory)((0, fs_store_1.livingCareRecordDataDir)("memory-corrections"));
}
