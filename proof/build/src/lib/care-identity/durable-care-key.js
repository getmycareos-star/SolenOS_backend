"use strict";
/**

 * Contributor session key (browser) — distinct from Care Reality (`care_recipient_id`).

 * Locked A: interaction session is temporary; durable care key is not.

 * Locked B: many contributors → one Living Care Record keyed by care recipient.

 */
Object.defineProperty(exports, "__esModule", { value: true });
exports.INTERACTION_SESSION_STORAGE = exports.CARE_RECIPIENT_ID_STORAGE = exports.DURABLE_CARE_KEY_STORAGE = exports.DEFAULT_DURABLE_CARE_KEY = void 0;
exports.mintDurableCareKey = mintDurableCareKey;
exports.mintInteractionSessionId = mintInteractionSessionId;
exports.isInteractionSessionId = isInteractionSessionId;
exports.resolveDurableCareKey = resolveDurableCareKey;
exports.requireCareKeyFromRequest = requireCareKeyFromRequest;
exports.ensureClientDurableCareKey = ensureClientDurableCareKey;
exports.ensureClientInteractionSessionId = ensureClientInteractionSessionId;
exports.careSessionIdForDurableKey = careSessionIdForDurableKey;
exports.resolveInteractionSessionId = resolveInteractionSessionId;
exports.DEFAULT_DURABLE_CARE_KEY = "default_caregiver";
exports.DURABLE_CARE_KEY_STORAGE = "solenos_durable_care_key";
/** Persisted Care Reality id — many contributors join one Living Care Record (Locked B). */
exports.CARE_RECIPIENT_ID_STORAGE = "solenos_care_recipient_id";
exports.INTERACTION_SESSION_STORAGE = "solenos_care_session_id";
function isEmptyKey(value) {
    return !value?.trim();
}
function newRandomId() {
    if (typeof globalThis.crypto?.randomUUID === "function") {
        return globalThis.crypto.randomUUID();
    }
    return `${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 10)}`;
}
/** Mint a per-browser durable care key. */
function mintDurableCareKey() {
    return `care_${newRandomId()}`;
}
/** Mint a temporary interaction session (Begin / resume). Never a care reality id. */
function mintInteractionSessionId() {
    return `sess_${newRandomId()}`;
}
function isInteractionSessionId(value) {
    return Boolean(value?.trim().startsWith("sess_"));
}
/**

 * Resolve an existing care key for API/server paths.

 * Does not mint — returns DEFAULT only when nothing is provided (legacy helpers).

 */
function resolveDurableCareKey(params) {
    const fromCaregiver = params.caregiver_id?.trim();
    if (fromCaregiver)
        return fromCaregiver;
    const fromSession = params.care_session_id?.trim();
    // Interaction sessions are not care reality keys (Locked A).
    if (fromSession && !isInteractionSessionId(fromSession))
        return fromSession;
    return exports.DEFAULT_DURABLE_CARE_KEY;
}
/**

 * Caregiver-facing API paths: require an explicit care key.

 * Missing key → fail (never invent shared default_caregiver).

 * Explicit `default_caregiver` remains allowed for verify scripts.

 * Ephemeral `sess_*` ids are never accepted as the durable care key.

 */
function requireCareKeyFromRequest(params) {
    const fromCaregiver = params.caregiver_id?.trim();
    if (fromCaregiver)
        return { ok: true, careKey: fromCaregiver };
    const fromSession = params.care_session_id?.trim();
    if (fromSession && !isInteractionSessionId(fromSession)) {
        return { ok: true, careKey: fromSession };
    }
    return {
        ok: false,
        error: "caregiver_id is required — Living Care Record writes need a care key",
    };
}
/**

 * Browser caregiver path: reuse the stored durable key, or mint when missing.

 * Locked A: never remint on Begin — same identity keeps Care Reality.

 * `default_caregiver` is a valid stored identity (demo/verify); do not orphan it.

 */
function ensureClientDurableCareKey(stored) {
    if (!isEmptyKey(stored))
        return stored.trim();
    return mintDurableCareKey();
}
/**

 * Interaction session id — temporary; never the durable care key.

 * Begin (`forceNew`) starts a new session without touching Care Reality.

 */
function ensureClientInteractionSessionId(stored, options) {
    if (options?.forceNew)
        return mintInteractionSessionId();
    const trimmed = stored?.trim();
    if (trimmed && isInteractionSessionId(trimmed))
        return trimmed;
    // Legacy alias (session === care key) or missing → mint a true session id.
    return mintInteractionSessionId();
}
/**

 * @deprecated Prefer ensureClientInteractionSessionId.

 * Kept for call-site migration; does not return the durable key (Locked A).

 */
function careSessionIdForDurableKey(_durableCareKey) {
    void _durableCareKey;
    return mintInteractionSessionId();
}
/** Prefer client-provided session; mint only when absent. */
function resolveInteractionSessionId(careSessionId) {
    const trimmed = careSessionId?.trim();
    if (trimmed && isInteractionSessionId(trimmed))
        return trimmed;
    if (trimmed && !isInteractionSessionId(trimmed)) {
        // Legacy clients sent care key as session — mint a real session for this request.
        return mintInteractionSessionId();
    }
    return mintInteractionSessionId();
}
