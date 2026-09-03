import type {
  CompatibilityStatusValue,
  ConflictClaim,
  ConflictHistoryEntry,
  ConflictObject,
  ConflictResolutionStatusValue,
  ConflictTypeValue,
  ResolutionEvidence,
  TemporalScope,
} from "./types";

export function createConflictObject(
  conflictType: ConflictTypeValue,
  claims: ConflictClaim[],
  compatibilityStatus: CompatibilityStatusValue,
  explanation: string,
  temporalScope: TemporalScope | null,
): ConflictObject {
  const now = new Date().toISOString();
  const history: ConflictHistoryEntry[] = [
    {
      timestamp: now,
      action: "detected",
      from_status: null,
      to_status: "unresolved",
      reason: "Initial conflict detection",
      actor: "system",
    },
  ];

  return {
    conflict_id: `conf_${Date.now()}_${Math.random().toString(36).slice(2, 9)}`,
    conflict_type: conflictType,
    compatibility_status: compatibilityStatus,
    resolution_status: "unresolved",
    claims,
    temporal_scope: temporalScope,
    explanation,
    resolution_evidence: null,
    resolution_mechanism: null,
    detected_at: now,
    last_reviewed_at: now,
    history,
  };
}

export function transitionResolutionStatus(
  conflict: ConflictObject,
  newStatus: ConflictResolutionStatusValue,
  reason: string,
  mechanism: ResolutionEvidence["mechanism"] | null,
  evidenceClaimIds: string[],
  actor: ConflictHistoryEntry["actor"],
): ConflictObject {
  const now = new Date().toISOString();
  const previousStatus = conflict.resolution_status;

  const historyEntry: ConflictHistoryEntry = {
    timestamp: now,
    action: newStatus === "resolved" ? "resolved" : newStatus === "provisionally_resolved" ? "provisionally_resolved" : "reviewed",
    from_status: previousStatus,
    to_status: newStatus,
    reason,
    actor,
  };

  const updatedHistory = [...conflict.history, historyEntry];

  let resolution_evidence: ResolutionEvidence | null = null;
  if (mechanism && (newStatus === "resolved" || newStatus === "provisionally_resolved")) {
    resolution_evidence = {
      mechanism,
      evidence_claim_ids: evidenceClaimIds,
      description: reason,
      resolved_at: now,
      resolved_by: actor,
      previous_status: previousStatus,
    };
  }

  return {
    ...conflict,
    resolution_status: newStatus,
    resolution_evidence,
    resolution_mechanism: mechanism,
    last_reviewed_at: now,
    history: updatedHistory,
  };
}

export function reopenConflict(
  conflict: ConflictObject,
  reason: string,
  actor: ConflictHistoryEntry["actor"],
): ConflictObject {
  const now = new Date().toISOString();
  const previousStatus = conflict.resolution_status;

  const historyEntry: ConflictHistoryEntry = {
    timestamp: now,
    action: "reopened",
    from_status: previousStatus,
    to_status: "unresolved",
    reason,
    actor,
  };

  return {
    ...conflict,
    resolution_status: "unresolved",
    resolution_evidence: null,
    resolution_mechanism: null,
    last_reviewed_at: now,
    history: [...conflict.history, historyEntry],
  };
}

export function supersedeConflict(
  conflict: ConflictObject,
  supersededByClaimId: string,
  reason: string,
  actor: ConflictHistoryEntry["actor"],
): ConflictObject {
  const now = new Date().toISOString();

  const updatedClaims = conflict.claims.map((c) =>
    c.claim_id === supersededByClaimId ? { ...c, superseded_by: supersededByClaimId } : c,
  );

  const historyEntry: ConflictHistoryEntry = {
    timestamp: now,
    action: "superseded",
    from_status: conflict.resolution_status,
    to_status: "superseded",
    reason,
    actor,
  };

  return {
    ...conflict,
    claims: updatedClaims,
    resolution_status: "superseded",
    resolution_evidence: {
      mechanism: "explicit_correction",
      evidence_claim_ids: [supersededByClaimId],
      description: reason,
      resolved_at: now,
      resolved_by: actor,
      previous_status: conflict.resolution_status,
    },
    resolution_mechanism: "explicit_correction",
    last_reviewed_at: now,
    history: [...conflict.history, historyEntry],
  };
}

export function invalidateConflict(
  conflict: ConflictObject,
  reason: string,
  actor: ConflictHistoryEntry["actor"],
): ConflictObject {
  const now = new Date().toISOString();

  const historyEntry: ConflictHistoryEntry = {
    timestamp: now,
    action: "invalidated",
    from_status: conflict.resolution_status,
    to_status: "invalidated",
    reason,
    actor,
  };

  return {
    ...conflict,
    resolution_status: "invalidated",
    last_reviewed_at: now,
    history: [...conflict.history, historyEntry],
  };
}
