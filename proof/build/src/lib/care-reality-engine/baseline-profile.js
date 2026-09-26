"use strict";
/**
 * Phase 2 — Baseline Memory Profile.
 * Change detection needs "what normal looked like" — not long questionnaires.
 * Capture only important baseline domains from evidence over time.
 */
Object.defineProperty(exports, "__esModule", { value: true });
exports.BASELINE_PROFILE_DOMAINS = void 0;
exports.getBaselineProfile = getBaselineProfile;
exports.upsertBaselineProfileEntry = upsertBaselineProfileEntry;
exports.syncBaselineFromIntelligenceFacts = syncBaselineFromIntelligenceFacts;
exports.resetBaselineProfileStore = resetBaselineProfileStore;
const fs_store_1 = require("../living-care-record-persistence/fs-store");
exports.BASELINE_PROFILE_DOMAINS = [
    "communication",
    "mobility",
    "daily_routines",
    "preferences",
    "support_situation",
];
const memory = new Map();
function pathFor(id) {
    return (0, fs_store_1.livingCareRecordDataDir)("baseline-profile", `${(0, fs_store_1.sanitizeDurableCareKey)(id)}.json`);
}
function getBaselineProfile(careRecipientId) {
    const cached = memory.get(careRecipientId);
    if (cached)
        return cached;
    const durable = (0, fs_store_1.readDurableJson)(pathFor(careRecipientId));
    if (durable?.entries) {
        memory.set(careRecipientId, durable);
        return durable;
    }
    return null;
}
/**
 * Upsert a baseline fact from evidence — never questionnaire homework.
 * Domain must be inferred by callers from understanding, not keyword templates.
 */
function upsertBaselineProfileEntry(params) {
    const now = params.nowIso ?? new Date().toISOString();
    const summary = params.summary.trim().slice(0, 280);
    if (!summary) {
        return (getBaselineProfile(params.careRecipientId) ?? {
            care_recipient_id: params.careRecipientId,
            entries: [],
            established: false,
            updated_at: now,
        });
    }
    const prior = getBaselineProfile(params.careRecipientId) ??
        {
            care_recipient_id: params.careRecipientId,
            entries: [],
            established: false,
            updated_at: now,
        };
    const nextEntry = {
        domain: params.domain,
        summary,
        source_event_ids: params.sourceEventIds ?? [],
        updated_at: now,
        confidence: params.confidence ?? "low",
    };
    const entries = [
        ...prior.entries.filter((e) => e.domain !== params.domain),
        nextEntry,
    ];
    const profile = {
        care_recipient_id: params.careRecipientId,
        entries,
        established: entries.length >= 1,
        updated_at: now,
    };
    memory.set(params.careRecipientId, profile);
    (0, fs_store_1.writeDurableJson)(pathFor(params.careRecipientId), profile);
    return profile;
}
/** Seed baseline from prior baseline-intelligence facts when available — no phrase templates. */
function syncBaselineFromIntelligenceFacts(params) {
    const domainMap = {
        communication: "communication",
        mobility: "mobility",
        sleep: "daily_routines",
        routine: "daily_routines",
        daily_living: "daily_routines",
        preference: "preferences",
        preferences: "preferences",
        support: "support_situation",
        caregiving: "support_situation",
    };
    let last = getBaselineProfile(params.careRecipientId);
    for (const fact of params.facts.slice(-12)) {
        const domain = domainMap[fact.domain.toLowerCase()];
        if (!domain || !fact.label.trim())
            continue;
        last = upsertBaselineProfileEntry({
            careRecipientId: params.careRecipientId,
            domain,
            summary: fact.label,
            sourceEventIds: fact.source_event_ids,
            confidence: fact.confidence ?? "medium",
            nowIso: params.nowIso,
        });
    }
    return last;
}
function resetBaselineProfileStore() {
    memory.clear();
    (0, fs_store_1.clearDurableDirectory)((0, fs_store_1.livingCareRecordDataDir)("baseline-profile"));
}
