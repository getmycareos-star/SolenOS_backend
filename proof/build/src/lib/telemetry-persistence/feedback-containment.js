"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.getFeedbackContainmentRecord = getFeedbackContainmentRecord;
exports.shouldApplyFeedbackContainment = shouldApplyFeedbackContainment;
exports.setFeedbackContainmentFromFeedback = setFeedbackContainmentFromFeedback;
exports.peekFeedbackContainmentAdaptation = peekFeedbackContainmentAdaptation;
exports.applyFeedbackContainmentToRelief = applyFeedbackContainmentToRelief;
exports.consumeFeedbackContainment = consumeFeedbackContainment;
exports.resetFeedbackContainmentStore = resetFeedbackContainmentStore;
/**
 * Phase 5.3 — Feedback → load/containment only (not empathy training).
 * Confusion signal → hold Clarity + reduce asks for one interaction turn.
 * Helpful feedback → no disclosure change (avoid engagement hack).
 *
 * SoT: docs/17-canonical-architecture/spine-build-sequence.md Slice 5.3
 */
const fs_store_1 = require("../living-care-record-persistence/fs-store");
const multi_caregiver_context_model_1 = require("../multi-caregiver-context-model");
const memory = new Map();
function normalizeCareKey(careKey) {
    return (0, multi_caregiver_context_model_1.resolveCareRealityStoreKey)(careKey.trim());
}
function filePath(careKey) {
    return (0, fs_store_1.livingCareRecordDataDir)("feedback-containment", `${(0, fs_store_1.sanitizeDurableCareKey)(normalizeCareKey(careKey))}.json`);
}
function emptyRecord(careKey) {
    return {
        care_key: careKey,
        hold_clarity: false,
        max_asks_cap: 3,
        reason: "none",
        set_at: new Date().toISOString(),
        pending: false,
    };
}
function getFeedbackContainmentRecord(careKey) {
    const key = normalizeCareKey(careKey);
    const cached = memory.get(key);
    if (cached)
        return cached;
    const durable = (0, fs_store_1.readDurableJson)(filePath(key));
    if (durable) {
        memory.set(key, durable);
        return durable;
    }
    return emptyRecord(key);
}
function persist(record) {
    memory.set(record.care_key, record);
    (0, fs_store_1.writeDurableJson)(filePath(record.care_key), record);
    return record;
}
/**
 * Confusion feedback only — helpful alone never changes disclosure.
 */
function shouldApplyFeedbackContainment(feedback) {
    return feedback.reduced_confusion_yes_no === false;
}
/** Record one-turn containment from POST /api/feedback (requires care_key). */
function setFeedbackContainmentFromFeedback(params) {
    if (!shouldApplyFeedbackContainment(params.feedback)) {
        return null;
    }
    const key = normalizeCareKey(params.careKey);
    const now = params.nowIso ?? new Date().toISOString();
    return persist({
        care_key: key,
        hold_clarity: true,
        max_asks_cap: 0,
        reason: "confusion_feedback",
        set_at: now,
        pending: true,
    });
}
function peekFeedbackContainmentAdaptation(careKey) {
    const record = getFeedbackContainmentRecord(careKey);
    if (!record.pending || record.reason !== "confusion_feedback") {
        return {
            active: false,
            hold_clarity: false,
            max_asks_cap: 3,
            reason: "none",
        };
    }
    return {
        active: true,
        hold_clarity: record.hold_clarity,
        max_asks_cap: record.max_asks_cap,
        reason: record.reason,
    };
}
/** Apply load/containment only — never copy or empathy templates. */
function applyFeedbackContainmentToRelief(decision, adaptation) {
    if (!adaptation.active)
        return decision;
    const max_asks = Math.min(decision.max_asks, adaptation.max_asks_cap);
    return {
        ...decision,
        show_clarity: adaptation.hold_clarity ? false : decision.show_clarity,
        max_asks,
        show_asks: max_asks > 0 ? decision.show_asks : false,
        show_follow_up: adaptation.hold_clarity ? false : decision.show_follow_up,
    };
}
/** Mark one-turn containment consumed after disclosure applied. */
function consumeFeedbackContainment(careKey) {
    const record = getFeedbackContainmentRecord(careKey);
    if (!record.pending)
        return record;
    return persist({
        ...record,
        pending: false,
        hold_clarity: false,
        max_asks_cap: 3,
        reason: "none",
        set_at: new Date().toISOString(),
    });
}
function resetFeedbackContainmentStore() {
    memory.clear();
    (0, fs_store_1.clearDurableDirectory)((0, fs_store_1.livingCareRecordDataDir)("feedback-containment"));
}
