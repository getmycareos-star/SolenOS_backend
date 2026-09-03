// ---------------------------------------------------------------------------
// Care identity / continuity layer (Phase 10)
// In-memory store; safe to wire into the situation pipeline without
// requiring external storage. PostgreSQL migration is a follow-up.
// ---------------------------------------------------------------------------

export type CareIdentityRecord = {
  caregiver_id: string;
  care_recipient_id: string;
  created_at: string;
  last_seen_at: string;
  session_count: number;
};

export type CareIdentitySummary = {
  caregiver_id: string;
  care_recipient_id: string;
  session_count: number;
  is_returning: boolean;
  first_seen_at: string;
  last_seen_at: string;
};

export type ContinuityDecision = {
  decision: "new_caregiver" | "returning_caregiver" | "same_session";
  confidence: number;
  reason: string;
  session_count: number;
  last_interaction_at: string | null;
};

const identityStore = new Map<string, CareIdentityRecord>();

function nowIso(): string {
  return new Date().toISOString();
}

export function createCareIdentity(input: {
  caregiverId: string;
  careRecipientId: string;
}): CareIdentityRecord {
  const careRecipientId = input.careRecipientId?.trim() || input.caregiverId;
  const existing = identityStore.get(careRecipientId);
  if (existing) {
    existing.last_seen_at = nowIso();
    existing.session_count += 1;
    return existing;
  }
  const record: CareIdentityRecord = {
    caregiver_id: input.caregiverId,
    care_recipient_id: careRecipientId,
    created_at: nowIso(),
    last_seen_at: nowIso(),
    session_count: 1,
  };
  identityStore.set(careRecipientId, record);
  return record;
}

export function getCareIdentity(careRecipientId: string): CareIdentityRecord | null {
  return identityStore.get(careRecipientId) ?? null;
}

export function getCareIdentitySummary(careRecipientId: string): CareIdentitySummary | null {
  const record = identityStore.get(careRecipientId);
  if (!record) return null;
  return {
    caregiver_id: record.caregiver_id,
    care_recipient_id: record.care_recipient_id,
    session_count: record.session_count,
    is_returning: record.session_count > 1,
    first_seen_at: record.created_at,
    last_seen_at: record.last_seen_at,
  };
}

export function incrementSessionCount(careRecipientId: string): CareIdentityRecord {
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

export function detectContinuity(input: {
  caregiverId: string;
  careRecipientId: string;
  rawText: string;
}): ContinuityDecision {
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

export function resolveActiveCareRecipientId(input: {
  caregiver_id: string;
  care_recipient_id?: string | null;
}): string {
  return input.care_recipient_id?.trim() || input.caregiver_id;
}
