import type {
  ClaimDowngradeRecord,
  EvidenceStatus,
  ExtractionCandidate,
  RawInput,
  ValidatedCareEvent,
} from "./types";

/**
 * Source-Pointer Trust Layer — deterministic validator + enforcer.
 *
 * INVARIANT (non-negotiable):
 *   A claim with evidence_status = "confirmed" | "reported" MUST have a verified,
 *   exact source pointer into the original evidence.
 *
 * A source pointer is valid ONLY if:
 *   originalText.slice(start_offset, end_offset) === source_span
 * with NO normalization, trimming, paraphrasing, case conversion, whitespace
 * substitution, or fuzzy matching.
 *
 * The validator may ONLY DOWNGRADE evidence_status (never upgrade). It also
 * never manufacture confirmation from numeric confidence or repeated signals.
 */

export type PointerVerificationResult = {
  verified: boolean;
  reason:
    | "exact_match"
    | "missing_source_span"
    | "missing_start_offset"
    | "missing_end_offset"
    | "negative_start_offset"
    | "end_not_greater_than_start"
    | "end_beyond_original_text"
    | "span_mismatch";
};

/**
 * Verify that the candidate's source pointer exactly identifies a substring of
 * the original evidence text. Pure and deterministic.
 */
export function verifySourcePointer(
  sourceSpan: string,
  startOffset: number | null,
  endOffset: number | null,
  originalText: string,
): PointerVerificationResult {
  if (!sourceSpan) return { verified: false, reason: "missing_source_span" };
  if (startOffset === null || startOffset === undefined)
    return { verified: false, reason: "missing_start_offset" };
  if (endOffset === null || endOffset === undefined)
    return { verified: false, reason: "missing_end_offset" };
  if (startOffset < 0) return { verified: false, reason: "negative_start_offset" };
  if (endOffset <= startOffset)
    return { verified: false, reason: "end_not_greater_than_start" };
  if (endOffset > originalText.length)
    return { verified: false, reason: "end_beyond_original_text" };

  const actual = originalText.slice(startOffset, endOffset);
  if (actual !== sourceSpan) return { verified: false, reason: "span_mismatch" };

  return { verified: true, reason: "exact_match" };
}

const REQUIRES_POINTER: ReadonlySet<EvidenceStatus> = new Set(["confirmed", "reported"]);

/** Whether the given evidence status requires a verified source pointer. */
export function requiresSourcePointer(status: EvidenceStatus): boolean {
  return REQUIRES_POINTER.has(status);
}

/**
 * Deterministic evidence-status downgrade path.
 *
 * Any confirmed/reported claim whose pointer is missing or unverifiable is
 * downgraded to "unknown" (never deleted). Lower statuses are left untouched.
 *
 * Returns the (possibly unchanged) status and the downgrade reason (if any).
 */
export function enforceEvidenceStatus(
  status: EvidenceStatus,
  verification: PointerVerificationResult,
): { status: EvidenceStatus; reason: string | null } {
  if (!requiresSourcePointer(status)) {
    // inferred / unknown / contradictory => no pointer required, no downgrade.
    return { status, reason: null };
  }
  if (verification.verified) {
    return { status, reason: null };
  }
  return { status: "unknown", reason: verification.reason };
}

/**
 * Enforce the source-pointer invariant on a candidate.
 *
 * - Sets `source_span_verified` and the offsets.
 * - If the claim is confirmed/reported but the pointer is missing/invalid,
 *   downgrades evidence_status to "unknown".
 * - NEVER upgrades evidence_status.
 *
 * Returns the updated candidate plus an optional downgrade record.
 */
export function enforceSourcePointer(
  candidate: ExtractionCandidate,
  originalText: string,
  evidenceId: string | null,
): { candidate: ExtractionCandidate; downgrade: ClaimDowngradeRecord | null } {
  const verification = verifySourcePointer(
    candidate.source_span,
    candidate.source_span_start_offset,
    candidate.source_span_end_offset,
    originalText,
  );

  const { status: enforcedStatus, reason } = enforceEvidenceStatus(
    candidate.evidence_status,
    verification,
  );

  const updated: ExtractionCandidate = {
    ...candidate,
    source_span_verified: verification.verified,
    evidence_status: enforcedStatus,
  };

  if (enforcedStatus === candidate.evidence_status || reason === null) {
    // No downgrade (either already non-high-confidence, or pointer valid).
    return { candidate: updated, downgrade: null };
  }

  const downgrade: ClaimDowngradeRecord = {
    id: `downgrade_${Date.now()}_${Math.random().toString(36).slice(2, 9)}`,
    claim_id: candidate.id,
    evidence_id: evidenceId,
    original_confidence: candidate.confidence,
    final_confidence: candidate.confidence,
    original_status: candidate.evidence_status,
    final_status: enforcedStatus,
    reason,
    source_span_before: candidate.source_span,
    source_span_start_offset: candidate.source_span_start_offset,
    source_span_end_offset: candidate.source_span_end_offset,
    created_at: new Date().toISOString(),
  };

  return { candidate: updated, downgrade };
}

/**
 * Convenience: run the full enforce step over a list of candidates for a raw
 * input. Returns the enforced candidates and any downgrade records.
 */
export function enforceSourcePointersForRawInput<T extends { id: string }>(
  candidates: ExtractionCandidate[],
  rawInput: RawInput,
): { candidates: ExtractionCandidate[]; downgrades: ClaimDowngradeRecord[] } {
  const downgrades: ClaimDowngradeRecord[] = [];
  const enforced: ExtractionCandidate[] = candidates.map((c) => {
    const { candidate, downgrade } = enforceSourcePointer(c, rawInput.content, rawInput.id);
    if (downgrade) downgrades.push(downgrade);
    return candidate;
  });
  return { candidates: enforced, downgrades };
}

/**
 * Apply the invariant to a ValidatedCareEvent (the persisted truth layer).
 * A high-confidence validated event must still be traceable to a verified
 * source pointer. If not, downgrade to "unknown" (never delete the event).
 */
export function enforceValidatedEventPointer(
  event: ValidatedCareEvent,
  originalText: string | null,
): { event: ValidatedCareEvent; downgrade: ClaimDowngradeRecord | null } {
  if (!requiresSourcePointer(event.evidence_status) || originalText === null) {
    return { event, downgrade: null };
  }

  const verification = verifySourcePointer(
    event.extracted_fact,
    event.source_span_start_offset,
    event.source_span_end_offset,
    originalText,
  );
  const { status: enforcedStatus, reason } = enforceEvidenceStatus(
    event.evidence_status,
    verification,
  );

  const updated: ValidatedCareEvent = {
    ...event,
    source_span_verified: verification.verified,
    evidence_status: enforcedStatus,
  };

  if (enforcedStatus === event.evidence_status || reason === null) {
    return { event: updated, downgrade: null };
  }

  const downgrade: ClaimDowngradeRecord = {
    id: `downgrade_${Date.now()}_${Math.random().toString(36).slice(2, 9)}`,
    claim_id: event.id,
    evidence_id: event.raw_input_id,
    original_confidence: event.confidence_score,
    final_confidence: event.confidence_score,
    original_status: event.evidence_status,
    final_status: enforcedStatus,
    reason,
    source_span_before: event.extracted_fact,
    source_span_start_offset: event.source_span_start_offset,
    source_span_end_offset: event.source_span_end_offset,
    created_at: new Date().toISOString(),
  };

  return { event: updated, downgrade };
}
