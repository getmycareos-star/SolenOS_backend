/**
 * Care Identity — Phase 10.
 *
 * Tracks the identity lifecycle of a caregiver's relationship to a care recipient.
 * Enables the pipeline to distinguish new caregivers from returning ones, and to
 * branch behavior in the response composer based on continuity state.
 *
 * Locked B: keyed by care_recipient_id (shared Care Reality). Many contributors
 * join one Living Care Record; identity lives on the recipient side.
 *
 * In-memory store — mirrors `durable-care-key.ts` patterns. Durable persistence
 * (postgres) is added by the longitudinal-care-state primitive, not duplicated here.
 */

export type CareIdentity = {
  caregiver_id: string;
  care_recipient_id: string;
  created_at: string;
  first_session_at: string;
  last_session_at: string;
  session_count: number;
  is_returning: boolean;
};

export type CareIdentitySummary = {
  caregiver_id: string;
  care_recipient_id: string;
  session_count: number;
  is_returning: boolean;
  first_session_at: string;
  last_session_at: string;
};

export type ContinuityDecision = {
  kind: "NEW_CAREGIVER" | "RETURNING_CAREGIVER" | "REENTRY_HINT" | "CONTINUATION";
  reason: string;
  confidence: number;
  matched_session_at?: string | null;
};

const identitiesByCareRecipient = new Map<string, CareIdentity>();

function nowIso(): string {
  return new Date().toISOString();
}

function getOrInitIdentity(
  caregiverId: string,
  careRecipientId: string,
): CareIdentity {
  const existing = identitiesByCareRecipient.get(careRecipientId);
  if (existing && existing.caregiver_id === caregiverId) {
    return existing;
  }
  const created: CareIdentity = {
    caregiver_id: caregiverId,
    care_recipient_id: careRecipientId,
    created_at: nowIso(),
    first_session_at: nowIso(),
    last_session_at: nowIso(),
    session_count: 0,
    is_returning: false,
  };
  identitiesByCareRecipient.set(careRecipientId, created);
  return created;
}

export function getCareIdentity(
  careRecipientId: string,
): CareIdentity | null {
  return identitiesByCareRecipient.get(careRecipientId) ?? null;
}

export function createCareIdentity(params: {
  caregiverId: string;
  careRecipientId: string;
}): CareIdentity {
  return getOrInitIdentity(params.caregiverId, params.careRecipientId);
}

export function getCareIdentitySummary(
  careRecipientId: string,
): CareIdentitySummary | null {
  const id = identitiesByCareRecipient.get(careRecipientId);
  if (!id) return null;
  return {
    caregiver_id: id.caregiver_id,
    care_recipient_id: id.care_recipient_id,
    session_count: id.session_count,
    is_returning: id.is_returning,
    first_session_at: id.first_session_at,
    last_session_at: id.last_session_at,
  };
}

export function incrementSessionCount(careRecipientId: string): void {
  const id = identitiesByCareRecipient.get(careRecipientId);
  if (!id) return;
  id.session_count += 1;
  id.last_session_at = nowIso();
  id.is_returning = true;
}

const REENTRY_HINTS = [
  "where were we",
  "continue",
  "pick up",
  "what happened",
  "last time",
  "again",
];

export function detectContinuity(params: {
  caregiverId: string;
  careRecipientId: string;
  rawText: string;
}): ContinuityDecision {
  const identity = identitiesByCareRecipient.get(params.careRecipientId);
  const text = (params.rawText ?? "").toLowerCase().trim();

  const isReentryHint = REENTRY_HINTS.some((hint) => text.includes(hint));

  if (!identity) {
    return {
      kind: "NEW_CAREGIVER",
      reason: "no prior identity for this care recipient",
      confidence: 0.95,
    };
  }

  if (isReentryHint) {
    return {
      kind: "REENTRY_HINT",
      reason: "input contains continuity hint phrase",
      confidence: 0.8,
      matched_session_at: identity.last_session_at,
    };
  }

  if (identity.session_count > 1) {
    return {
      kind: "RETURNING_CAREGIVER",
      reason: "prior sessions exist for this care recipient",
      confidence: 0.85,
      matched_session_at: identity.last_session_at,
    };
  }

  return {
    kind: "CONTINUATION",
    reason: "first session — continuing within current interaction",
    confidence: 0.7,
  };
}

export function resolveActiveCareRecipientId(params: {
  caregiverId: string;
  careRecipientId?: string | null;
}): string {
  if (params.careRecipientId?.trim()) return params.careRecipientId;
  const identity = Array.from(identitiesByCareRecipient.values()).find(
    (id) => id.caregiver_id === params.caregiverId,
  );
  return identity?.care_recipient_id ?? params.caregiverId;
}

export type RecordIdentityCareEventInput = {
  care_recipient_id: string;
  event_id: string;
  occurred_at?: string;
};

export function recordCareEvent(
  input: RecordIdentityCareEventInput,
): void {
  const id = identitiesByCareRecipient.get(input.care_recipient_id);
  if (!id) return;
  id.last_session_at = input.occurred_at ?? nowIso();
}