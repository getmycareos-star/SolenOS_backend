import type { ConflictClaim, TemporalAssertion } from "./types";

/**
 * Temporal Analysis — determines how time context affects conflict interpretation.
 *
 * KEY RULE: Document date ≠ Event date.
 *
 * A document created on August 20 may describe an event from August 10.
 * The latest document does NOT automatically represent the latest reality.
 */

export type TemporalAnalysisResult = {
  interpretation: "historical_difference" | "legitimate_state_transition" | "true_contradiction" | "unresolved_temporal_conflict";
  explanation: string;
  confidence: number;
  document_dates: string[];
  event_dates: string[];
  temporal_gaps: Array<{ doc_date: string; event_date: string; gap_days: number }>;
};

export function analyzeTemporalContext(claims: ConflictClaim[]): TemporalAnalysisResult {
  const documentDates: string[] = [];
  const eventDates: string[] = [];
  const temporalGaps: TemporalAnalysisResult["temporal_gaps"] = [];

  for (const claim of claims) {
    if (claim.temporal_assertion) {
      if (claim.temporal_assertion.kind === "document_time" && claim.temporal_assertion.value) {
        documentDates.push(claim.temporal_assertion.value);
      }
      if (claim.temporal_assertion.kind === "event_time" && claim.temporal_assertion.value) {
        eventDates.push(claim.temporal_assertion.value);
      }
      if (claim.temporal_assertion.kind === "approximate" && claim.temporal_assertion.value) {
        eventDates.push(claim.temporal_assertion.value);
      }
    }
  }

  for (const docDate of documentDates) {
    for (const eventDate of eventDates) {
      const gap = dateGapDays(docDate, eventDate);
      if (gap !== null) {
        temporalGaps.push({ doc_date: docDate, event_date: eventDate, gap_days: gap });
      }
    }
  }

  if (claims.length < 2) {
    return {
      interpretation: "unresolved_temporal_conflict",
      explanation: "Insufficient claims for temporal analysis.",
      confidence: 0,
      document_dates: documentDates,
      event_dates: eventDates,
      temporal_gaps: temporalGaps,
    };
  }

  const hasOnlyDocumentDates = eventDates.length === 0 && documentDates.length > 0;
  const hasOnlyEventDates = documentDates.length === 0 && eventDates.length > 0;
  const hasMixed = documentDates.length > 0 && eventDates.length > 0;

  if (hasMixed) {
    return {
      interpretation: "unresolved_temporal_conflict",
      explanation: "Mixed document and event dates present. Cannot determine event ordering without explicit event dates for all claims.",
      confidence: 0.3,
      document_dates: documentDates,
      event_dates: eventDates,
      temporal_gaps: temporalGaps,
    };
  }

  if (hasOnlyEventDates) {
    const sorted = [...eventDates].sort();
    if (sorted.length >= 2) {
      const earliest = sorted[0]!;
      const latest = sorted[sorted.length - 1]!;
      const gap = dateGapDays(earliest, latest);
      if (gap !== null && gap > 0) {
        return {
          interpretation: "legitimate_state_transition",
          explanation: `Claims refer to different event times (${earliest} vs ${latest}). This is a state transition, not a contradiction.`,
          confidence: 0.8,
          document_dates: documentDates,
          event_dates: eventDates,
          temporal_gaps: temporalGaps,
        };
      }
    }
  }

  if (hasOnlyDocumentDates) {
    return {
      interpretation: "unresolved_temporal_conflict",
      explanation: "Only document dates available. Document date does not establish event date. Cannot determine if claims are temporally ordered.",
      confidence: 0.2,
      document_dates: documentDates,
      event_dates: eventDates,
      temporal_gaps: temporalGaps,
    };
  }

  return {
    interpretation: "unresolved_temporal_conflict",
    explanation: "No temporal assertions found. Cannot determine temporal ordering.",
    confidence: 0,
    document_dates: documentDates,
    event_dates: eventDates,
    temporal_gaps: temporalGaps,
  };
}

function dateGapDays(a: string, b: string): number | null {
  const da = new Date(a);
  const db = new Date(b);
  if (isNaN(da.getTime()) || isNaN(db.getTime())) return null;
  return Math.abs(da.getTime() - db.getTime()) / (1000 * 60 * 60 * 24);
}

export function createTemporalAssertion(
  kind: TemporalAssertion["kind"],
  value: string | null,
  confidence: number,
): TemporalAssertion {
  return {
    kind,
    value,
    is_range: false,
    range_start: null,
    range_end: null,
    confidence,
  };
}

export function createRangeTemporalAssertion(
  kind: TemporalAssertion["kind"],
  rangeStart: string | null,
  rangeEnd: string | null,
  confidence: number,
): TemporalAssertion {
  return {
    kind,
    value: null,
    is_range: true,
    range_start: rangeStart,
    range_end: rangeEnd,
    confidence,
  };
}
