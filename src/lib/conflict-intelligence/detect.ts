import type {
  CompatibilityStatusValue,
  ConflictClaim,
  ConflictObject,
  ConflictResolutionStatusValue,
  ConflictTypeValue,
  DetectConflictsInput,
  DetectConflictsOutput,
  TemporalScope,
} from "./types";
import { analyzeCompatibility } from "./compatibility";
import { analyzeTemporalContext } from "./temporal-analysis";
import { createConflictObject, reopenConflict } from "./conflict-object";
import { generateConflictExplanation } from "./explanation";

export function detectConflicts(input: DetectConflictsInput): DetectConflictsOutput {
  const { new_claim, existing_claims, temporal_context } = input;

  const comparable = findComparableClaims(new_claim, existing_claims);
  if (comparable.length === 0) {
    return { conflicts: [], apparent_conflicts_resolved: [], no_conflict: true };
  }

  const conflicts: ConflictObject[] = [];
  const apparentConflictsResolved: DetectConflictsOutput["apparent_conflicts_resolved"] = [];

  for (const existing of comparable) {
    const compatibility = analyzeCompatibility({
      claim_a: new_claim,
      claim_b: existing,
      temporal_context: {
        current_time: temporal_context.current_time,
        claim_a_temporal: new_claim.temporal_assertion,
        claim_b_temporal: existing.temporal_assertion,
      },
    });

    if (compatibility.status === "compatible") {
      continue;
    }

    const temporalAnalysis = analyzeTemporalContext([new_claim, existing]);

    let conflictType = inferConflictType(new_claim, existing);

    if (compatibility.status === "apparent_conflict") {
      const resolved = attemptApparentConflictResolution(
        new_claim,
        existing,
        compatibility,
        temporalAnalysis,
      );

      if (resolved) {
        apparentConflictsResolved.push(resolved);
        continue;
      }
    }

    const temporalScope = buildTemporalScope(new_claim, existing, temporalAnalysis);

    const explanation = generateConflictExplanation(
      createConflictObject(conflictType, [new_claim, existing], compatibility.status, "", temporalScope),
    );

    const conflict = createConflictObject(
      conflictType,
      [new_claim, existing],
      compatibility.status,
      explanation.summary,
      temporalScope,
    );

    conflicts.push(conflict);
  }

  return {
    conflicts,
    apparent_conflicts_resolved: apparentConflictsResolved,
    no_conflict: conflicts.length === 0,
  };
}

function findComparableClaims(new_claim: ConflictClaim, existing: ConflictClaim[]): ConflictClaim[] {
  return existing.filter((existing) => {
    if (new_claim.subject !== existing.subject) return false;
    if (new_claim.predicate !== existing.predicate) return false;
    if (new_claim.claim_id === existing.claim_id) return false;
    return true;
  });
}

function inferConflictType(a: ConflictClaim, b: ConflictClaim): ConflictTypeValue {
  if (a.predicate === "is_current" || a.predicate === "is_active" || a.predicate === "is_discontinued") {
    return "state";
  }
  if (a.predicate.includes("diagnosis") || b.predicate.includes("diagnosis")) {
    return "diagnostic";
  }
  if (a.predicate.includes("date") || b.predicate.includes("date")) {
    return "temporal";
  }
  if (a.predicate.includes("provider") || b.predicate.includes("provider") || a.predicate.includes("identity")) {
    return "identity";
  }
  if (a.predicate.includes("hospitalization") || b.predicate.includes("hospitalization")) {
    return "event";
  }
  if (a.predicate.includes("treatment") || b.predicate.includes("treatment")) {
    return "outcome";
  }
  if (a.predicate.includes("dose") || a.predicate.includes("amount") || a.predicate.includes("value")) {
    return "quantitative";
  }
  if (a.predicate.includes("feels") || a.predicate.includes("is_doing") || b.predicate.includes("feels")) {
    return "subjective";
  }
  return "state";
}

function attemptApparentConflictResolution(
  a: ConflictClaim,
  b: ConflictClaim,
  compatibility: { reconciling_factors: string[] },
  temporalAnalysis: ReturnType<typeof analyzeTemporalContext>,
): { conflict_id: string; resolution: "temporal_clarification" | "state_transition_identified" | "specificity_reconciled"; explanation: string } | null {
  const reconciling = compatibility.reconciling_factors;

  if (reconciling.includes("temporal_change")) {
    return {
      conflict_id: `conf_apparent_${a.claim_id}_${b.claim_id}`,
      resolution: "state_transition_identified",
      explanation: "Claims represent a state transition over time, not a contradiction.",
    };
  }

  if (reconciling.includes("specificity_difference")) {
    return {
      conflict_id: `conf_apparent_${a.claim_id}_${b.claim_id}`,
      resolution: "specificity_reconciled",
      explanation: "Claims differ in specificity but are not contradictory.",
    };
  }

  if (temporalAnalysis.interpretation === "legitimate_state_transition") {
    return {
      conflict_id: `conf_apparent_${a.claim_id}_${b.claim_id}`,
      resolution: "temporal_clarification",
      explanation: temporalAnalysis.explanation,
    };
  }

  if (reconciling.includes("subjective_perspective")) {
    return {
      conflict_id: `conf_apparent_${a.claim_id}_${b.claim_id}`,
      resolution: "specificity_reconciled",
      explanation: "Claims represent subjective perspectives, not contradictory facts.",
    };
  }

  return null;
}

function buildTemporalScope(
  a: ConflictClaim,
  b: ConflictClaim,
  temporalAnalysis: ReturnType<typeof analyzeTemporalContext>,
): TemporalScope | null {
  if (temporalAnalysis.interpretation === "legitimate_state_transition") {
    const dates = [...temporalAnalysis.document_dates, ...temporalAnalysis.event_dates].sort();
    if (dates.length >= 2) {
      return {
        kind: "range",
        value: null,
        range_start: dates[0]!,
        range_end: dates[dates.length - 1]!,
        confidence: temporalAnalysis.confidence,
      };
    }
  }

  if (a.temporal_assertion?.value && b.temporal_assertion?.value) {
    return {
      kind: "range",
      value: null,
      range_start: a.temporal_assertion.value,
      range_end: b.temporal_assertion.value,
      confidence: 0.5,
    };
  }

  return null;
}

export function checkConflictReopening(
  newClaim: ConflictClaim,
  existingConflicts: ConflictObject[],
): { updatedConflicts: ConflictObject[]; newConflicts: ConflictObject[] } {
  const updated: ConflictObject[] = [];
  const newConflicts: ConflictObject[] = [];

  const related = existingConflicts.filter((c) => {
    if (c.resolution_status === "invalidated") return false;
    return c.claims.some((claim) => claim.subject === newClaim.subject && claim.predicate === newClaim.predicate);
  });

  for (const conflict of related) {
    if (conflict.resolution_status === "resolved" || conflict.resolution_status === "provisionally_resolved") {
      const compatibility = analyzeCompatibility({
        claim_a: newClaim,
        claim_b: conflict.claims[0]!,
        temporal_context: {
          current_time: new Date().toISOString(),
          claim_a_temporal: newClaim.temporal_assertion,
          claim_b_temporal: conflict.claims[0]!.temporal_assertion,
        },
      });

      if (compatibility.status === "genuine_conflict") {
        const updatedConflict = reopenConflict(
          conflict,
          `New evidence introduces conflict: ${newClaim.raw_text}`,
          "system",
        );
        updated.push(updatedConflict);
      }
    }
  }

  const remaining = existingConflicts.filter((c) => !related.includes(c));
  return { updatedConflicts: updated, newConflicts: [...remaining, ...newConflicts] };
}
