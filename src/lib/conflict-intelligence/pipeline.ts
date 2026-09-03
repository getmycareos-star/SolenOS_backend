import type {
  CompatibilityStatusValue,
  ConflictClaim,
  ConflictObject,
  ConflictResolutionStatusValue,
  ConflictResolutionInput,
  ConflictTypeValue,
} from "./types";
import { detectConflicts, checkConflictReopening } from "./detect";
import {
  transitionResolutionStatus,
  reopenConflict,
  supersedeConflict,
  invalidateConflict,
} from "./conflict-object";
import { generateConflictExplanation } from "./explanation";

export type ConflictIntelligencePipelineInput = {
  newClaim: ConflictClaim;
  existingClaims: ConflictClaim[];
  existingConflicts: ConflictObject[];
  temporalContext: {
    current_time: string;
    event_timeline: Array<{ time: string; event: string; source_id: string }>;
  };
};

export type ConflictIntelligencePipelineOutput = {
  newConflicts: ConflictObject[];
  updatedConflicts: ConflictObject[];
  apparentConflictsResolved: Array<{
    conflictId: string;
    resolution: string;
    explanation: string;
  }>;
  hasUnresolvedConflicts: boolean;
  explanation: ReturnType<typeof generateConflictExplanation> | null;
};

export function runConflictIntelligence(
  input: ConflictIntelligencePipelineInput,
): ConflictIntelligencePipelineOutput {
  const { newClaim, existingClaims, existingConflicts, temporalContext } = input;

  const detectionInput = {
    new_claim: newClaim,
    existing_claims: existingClaims,
    temporal_context: temporalContext,
  };

  const detectionOutput = detectConflicts(detectionInput);

  const reopeningResult = checkConflictReopening(newClaim, existingConflicts);

  const allNewConflicts = [...detectionOutput.conflicts, ...reopeningResult.newConflicts];
  const allUpdatedConflicts = reopeningResult.updatedConflicts;

  let explanation = null;
  if (allNewConflicts.length > 0) {
    explanation = generateConflictExplanation(allNewConflicts[0]!);
  }

  const apparentConflictsResolved = detectionOutput.apparent_conflicts_resolved.map((item) => ({
    conflictId: item.conflict_id,
    resolution: item.resolution,
    explanation: item.explanation,
  }));

  return {
    newConflicts: allNewConflicts,
    updatedConflicts: allUpdatedConflicts,
    apparentConflictsResolved,
    hasUnresolvedConflicts: allNewConflicts.some((c) => c.resolution_status === "unresolved"),
    explanation,
  };
}

export function resolveConflict(
  conflict: ConflictObject,
  input: ConflictResolutionInput,
): ConflictObject {
  switch (input.mechanism) {
    case "temporal_clarification":
    case "state_transition_identified":
    case "specificity_reconciled":
      return transitionResolutionStatus(
        conflict,
        "provisionally_resolved",
        input.reason,
        input.mechanism,
        input.evidenceClaimIds,
        input.actor,
      );
    case "explicit_correction":
    case "direct_observation":
    case "user_confirmation":
      return transitionResolutionStatus(
        conflict,
        "resolved",
        input.reason,
        input.mechanism,
        input.evidenceClaimIds,
        input.actor,
      );
    case "corroboration":
      return transitionResolutionStatus(
        conflict,
        "resolved",
        input.reason,
        input.mechanism,
        input.evidenceClaimIds,
        input.actor,
      );
    default:
      return transitionResolutionStatus(
        conflict,
        "provisionally_resolved",
        input.reason,
        input.mechanism,
        input.evidenceClaimIds,
        input.actor,
      );
  }
}

export function applyExplicitCorrection(
  conflict: ConflictObject,
  supersedingClaimId: string,
  reason: string,
  actor: ConflictResolutionInput["actor"],
): ConflictObject {
  return supersedeConflict(conflict, supersedingClaimId, reason, actor);
}

export function invalidateConflictObject(
  conflict: ConflictObject,
  reason: string,
  actor: ConflictResolutionInput["actor"],
): ConflictObject {
  return invalidateConflict(conflict, reason, actor);
}
