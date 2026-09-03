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
  getCareIdentity,
  createCareIdentity,
  getCareIdentitySummary,
  incrementSessionCount,
  detectContinuity,
  resolveActiveCareRecipientId,
  recordCareEvent,
  type CareIdentity,
  type CareIdentitySummary,
  type ContinuityDecision,
  type RecordIdentityCareEventInput,
} from "./identity";