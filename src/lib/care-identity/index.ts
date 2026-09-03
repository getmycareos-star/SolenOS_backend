export {
  DEFAULT_DURABLE_CARE_KEY,
  DURABLE_CARE_KEY_STORAGE,
  CARE_RECIPIENT_ID_STORAGE,
  INTERACTION_SESSION_STORAGE,
  resolveDurableCareKey,
  requireCareKeyFromRequest,
  mintDurableCareKey,
  mintInteractionSessionId,
  isInteractionSessionId,
  ensureClientDurableCareKey,
  ensureClientInteractionSessionId,
  careSessionIdForDurableKey,
  resolveInteractionSessionId,
} from "./durable-care-key";

export {
  createCareIdentity,
  getCareIdentity,
  getCareIdentitySummary,
  incrementSessionCount,
  detectContinuity,
  resolveActiveCareRecipientId,
  type CareIdentityRecord,
  type CareIdentitySummary,
  type ContinuityDecision,
} from "./identity-continuity";
