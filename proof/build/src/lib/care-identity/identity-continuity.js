"use strict";
// ---------------------------------------------------------------------------
// Care identity / continuity layer (Phase 10)
// In-memory store; safe to wire into the situation pipeline without
// requiring external storage. PostgreSQL migration is a follow-up.
// ---------------------------------------------------------------------------
Object.defineProperty(exports, "__esModule", { value: true });
exports.createCareIdentity = createCareIdentity;
exports.getCareIdentity = getCareIdentity;
exports.getCareIdentitySummary = getCareIdentitySummary;
exports.incrementSessionCount = incrementSessionCount;
exports.detectContinuity = detectContinuity;
exports.resolveActiveCareRecipientId = resolveActiveCareRecipientId;
const identityStore = new Map();
function nowIso() {
    return new Date().toISOString();
}
function createCareIdentity(input) {
    const careRecipientId = input.careRecipientId?.trim() || input.caregiverId;
    const existing = identityStore.get(careRecipientId);
    if (existing) {
        existing.last_seen_at = nowIso();
        existing.session_count += 1;
        return existing;
    }
    const record = {
        caregiver_id: input.caregiverId,
        care_recipient_id: careRecipientId,
        created_at: nowIso(),
        last_seen_at: nowIso(),
        session_count: 1,
    };
    identityStore.set(careRecipientId, record);
    return record;
}
function getCareIdentity(careRecipientId) {
    return identityStore.get(careRecipientId) ?? null;
}
function getCareIdentitySummary(careRecipientId) {
    const record = identityStore.get(careRecipientId);
    if (!record)
        return null;
    return {
        caregiver_id: record.caregiver_id,
        care_recipient_id: record.care_recipient_id,
        session_count: record.session_count,
        is_returning: record.session_count > 1,
        first_seen_at: record.created_at,
        last_seen_at: record.last_seen_at,
    };
}
function incrementSessionCount(careRecipientId) {
    const existing = identityStore.get(careRecipientId);
    if (existing) {
        existing.session_count += 1;
        existing.last_seen_at = nowIso();
        return existing;
    }
    return createCareIdentity({
        caregiverId: careRecipientId,
        careRecipientId,
    });
}
function detectContinuity(input) {
    const record = identityStore.get(input.careRecipientId);
    if (!record) {
        return {
            decision: "new_caregiver",
            confidence: 0.95,
            reason: "no_prior_identity",
            session_count: 0,
            last_interaction_at: null,
        };
    }
    if (record.session_count > 1) {
        return {
            decision: "returning_caregiver",
            confidence: 0.85,
            reason: "prior_sessions",
            session_count: record.session_count,
            last_interaction_at: record.last_seen_at,
        };
    }
    return {
        decision: "same_session",
        confidence: 0.9,
        reason: "first_session",
        session_count: record.session_count,
        last_interaction_at: record.last_seen_at,
    };
}
function resolveActiveCareRecipientId(input) {
    return input.care_recipient_id?.trim() || input.caregiver_id;
}
