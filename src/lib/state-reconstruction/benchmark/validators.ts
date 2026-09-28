/**
 * State Reconstruction — Failure Taxonomy Validators
 * SoT: docs/02-product/solenos-state-reconstruction.md
 */

import type {
  CareState,
  DomainState,
  Claim,
  Contradiction,
  ReconstructionResult,
} from "../types";

import { FAILURE_TAXONOMY } from "../contract-constants";

/**
 * Validate a reconstructed care state against all failure modes
 */
export function validateCareState(careState: CareState, claims: Claim[], contradictions: Contradiction[]): string[] {
  const failures: string[] = [];

  // 1. STATE_RECONSTRUCTION_FAILURE - General failure
  if (careState.failures.includes("STATE_RECONSTRUCTION_FAILURE")) {
    failures.push("STATE_RECONSTRUCTION_FAILURE");
  }

  // 2. SNAPSHOT_STATE_ERROR - Only latest note used
  if (isSnapshotOnly(careState, claims)) {
    failures.push("SNAPSHOT_STATE_ERROR");
  }

  // 3. LATEST_NOTE_AS_STATE - Latest note treated as state
  if (isLatestNoteAsState(careState, claims)) {
    failures.push("LATEST_NOTE_AS_STATE");
  }

  // 4. HISTORICAL_STATE_AS_CURRENT - Historical presented as current
  if (isHistoricalAsCurrent(careState)) {
    failures.push("HISTORICAL_STATE_AS_CURRENT");
  }

  // 5. CURRENT_STATE_AS_HISTORY - Current state mislabeled as history
  if (isCurrentAsHistory(careState)) {
    failures.push("CURRENT_STATE_AS_HISTORY");
  }

  // 6. STATE_SUPERSESSION_FAILURE - Supersession not handled
  if (hasSupersessionFailure(careState, claims)) {
    failures.push("STATE_SUPERSESSION_FAILURE");
  }

  // 7. STATE_CONTRADICTION_COLLAPSE - Contradictions forcibly resolved
  if (hasContradictionCollapse(careState, contradictions)) {
    failures.push("STATE_CONTRADICTION_COLLAPSE");
  }

  // 8. STATE_UNCERTAINTY_LOSS - Uncertainty not propagated
  if (hasUncertaintyLoss(careState, claims)) {
    failures.push("STATE_UNCERTAINTY_LOSS");
  }

  // 9. STATE_CONTEXT_LOSS - Context-specific states flattened
  if (hasContextLoss(careState)) {
    failures.push("STATE_CONTEXT_LOSS");
  }

  // 10. STATE_DOMAIN_COLLAPSE - Multiple domains collapsed
  if (hasDomainCollapse(careState, claims)) {
    failures.push("STATE_DOMAIN_COLLAPSE");
  }

  // 11. GLOBAL_STATE_OVERGENERALIZATION - Global state from specific
  if (hasGlobalOvergeneralization(careState)) {
    failures.push("GLOBAL_STATE_OVERGENERALIZATION");
  }

  // 12. FUNCTIONAL_STATE_ERROR - Functional state error
  if (hasFunctionalStateError(careState)) {
    failures.push("FUNCTIONAL_STATE_ERROR");
  }

  // 13. COGNITIVE_STATE_ERROR - Cognitive state error
  if (hasCognitiveStateError(careState)) {
    failures.push("COGNITIVE_STATE_ERROR");
  }

  // 14. MEDICATION_STATE_ERROR - Medication state error
  if (hasMedicationStateError(careState)) {
    failures.push("MEDICATION_STATE_ERROR");
  }

  // 15. CARE_NETWORK_STATE_ERROR - Care network state error
  if (hasCareNetworkStateError(careState)) {
    failures.push("CARE_NETWORK_STATE_ERROR");
  }

  // 16. OPERATIONAL_STATE_ERROR - Operational state error
  if (hasOperationalStateError(careState)) {
    failures.push("OPERATIONAL_STATE_ERROR");
  }

  // 17. BASELINE_STATE_LOSS - Baseline lost
  if (hasBaselineLoss(careState)) {
    failures.push("BASELINE_STATE_LOSS");
  }

  // 18. INTERMEDIATE_STATE_LOSS - Intermediate states lost
  if (hasIntermediateStateLoss(careState)) {
    failures.push("INTERMEDIATE_STATE_LOSS");
  }

  // 19. IMPROVEMENT_STATE_LOSS - Improvements not preserved
  if (hasImprovementLoss(careState)) {
    failures.push("IMPROVEMENT_STATE_LOSS");
  }

  // 20. STATE_FROM_DIAGNOSIS - State derived from diagnosis
  if (isStateFromDiagnosis(careState, claims)) {
    failures.push("STATE_FROM_DIAGNOSIS");
  }

  // 21. STATE_FROM_SINGLE_EVENT - State from single event
  if (isStateFromSingleEvent(careState, claims)) {
    failures.push("STATE_FROM_SINGLE_EVENT");
  }

  // 22. STATE_FROM_SINGLE_CLAIM - State from single claim
  if (isStateFromSingleClaim(careState)) {
    failures.push("STATE_FROM_SINGLE_CLAIM");
  }

  // 23. STALE_STATE_RECONSTRUCTION - Stale reconstruction
  if (isStaleReconstruction(careState)) {
    failures.push("STALE_STATE_RECONSTRUCTION");
  }

  // 24. UNSUPPORTED_CURRENT_STATE - Current state unsupported
  if (hasUnsupportedCurrentState(careState)) {
    failures.push("UNSUPPORTED_CURRENT_STATE");
  }

  // 25. STATE_PROVENANCE_LOSS - Provenance lost
  if (hasProvenanceLoss(careState)) {
    failures.push("STATE_PROVENANCE_LOSS");
  }

  // 26. STATE_TEMPORAL_ERROR - Temporal error
  if (hasTemporalError(careState)) {
    failures.push("STATE_TEMPORAL_ERROR");
  }

  // 27. STATE_DEPENDENCY_FAILURE - Dependency failure
  if (hasDependencyFailure(careState)) {
    failures.push("STATE_DEPENDENCY_FAILURE");
  }

  // 28. MIXED_CONTEXT_STATE_COLLAPSE - Mixed contexts collapsed
  if (hasMixedContextCollapse(careState)) {
    failures.push("MIXED_CONTEXT_STATE_COLLAPSE");
  }

  return failures;
}

/**
 * Check if reconstruction is snapshot-only (no trajectory)
 */
function isSnapshotOnly(careState: CareState, claims: Claim[]): boolean {
  if (claims.length <= 2) return false; // Not enough for trajectory

  const hasTrajectory = careState.domains.some((d) => d.trajectory.length > 1);
  return !hasTrajectory;
}

/**
 * Check if latest note treated as state
 */
function isLatestNoteAsState(careState: CareState, claims: Claim[]): boolean {
  const latestClaim = claims
    .filter((c) => c.status === "active")
    .sort((a, b) => new Date(b.valid_from).getTime() - new Date(a.valid_from).getTime())[0];

  if (!latestClaim) return false;

  // Check if any domain state matches latest claim exactly without synthesis
  return careState.domains.some((d) =>
    d.current_value === latestClaim.statement &&
    d.contextual_values.length <= 1
  );
}

/**
 * Check if historical state presented as current
 */
function isHistoricalAsCurrent(careState: CareState): boolean {
  return careState.domains.some((d) =>
    d.current_value && d.trajectory.length > 0 &&
    d.trajectory[0]?.temporal_status === "historical" &&
    d.current_value === d.trajectory[0].value
  );
}

/**
 * Check if current state mislabeled as history
 */
function isCurrentAsHistory(careState: CareState): boolean {
  return careState.domains.some((d) =>
    d.trajectory.some((t) =>
      t.temporal_status === "historical" &&
      new Date(t.timestamp).getTime() > Date.now() - 7 * 24 * 60 * 60 * 1000 // within 7 days
    )
  );
}

/**
 * Check for supersession failure
 */
function hasSupersessionFailure(careState: CareState, claims: Claim[]): boolean {
  const hasSupersededClaims = claims.some((c) => c.status === "superseded");
  const hasSupersessionInTrajectory = careState.domains.some((d) =>
    d.trajectory.some((t) => t.is_superseded)
  );
  return hasSupersededClaims && !hasSupersessionInTrajectory;
}

/**
 * Check for contradiction collapse
 */
function hasContradictionCollapse(careState: CareState, contradictions: Contradiction[]): boolean {
  if (contradictions.length === 0) return false;

  const preservedCount = contradictions.filter((c) => c.preserved).length;
  return preservedCount === 0;
}

/**
 * Check for uncertainty loss
 */
function hasUncertaintyLoss(careState: CareState, claims: Claim[]): boolean {
  const hasHighUncertaintyClaims = claims.some((c) => c.uncertainty === "high" || c.uncertainty === "unknown");
  const allLowUncertainty = careState.domains.every((d) =>
    d.uncertainty === "none" || d.uncertainty === "low"
  );
  return hasHighUncertaintyClaims && allLowUncertainty;
}

/**
 * Check for context loss
 */
function hasContextLoss(careState: CareState): boolean {
  return careState.domains.some((d) =>
    d.contextual_values.length > 1 &&
    d.contextual_values.every((c) => c.value === d.contextual_values[0].value)
  );
}

/**
 * Check for domain collapse
 */
function hasDomainCollapse(careState: CareState, claims: Claim[]): boolean {
  const domainsWithClaims = new Set(claims.map((c) => c.domain));
  const domainsWithState = new Set(
    careState.domains.filter((d) => d.current_value && d.current_value !== "No evidence").map((d) => d.domain)
  );
  return domainsWithClaims.size > domainsWithState.size;
}

/**
 * Check for global overgeneralization
 */
function hasGlobalOvergeneralization(careState: CareState): boolean {
  return careState.domains.some((d) =>
    d.current_value &&
    (d.current_value.includes("overall") || d.current_value.includes("general") || d.current_value === "stable") &&
    d.contextual_values.length > 1 &&
    new Set(d.contextual_values.map((c) => c.value)).size > 1
  );
}

/**
 * Check for functional state error
 */
function hasFunctionalStateError(careState: CareState): boolean {
  const functional = careState.domains.find((d) => d.domain === "functional");
  if (!functional) return false;

  // Functional state should be context-specific
  return functional.contextual_values.length > 0 &&
    functional.contextual_values.every((c) => !c.context.location && !c.context.assistive_device);
}

/**
 * Check for cognitive state error
 */
function hasCognitiveStateError(careState: CareState): boolean {
  const cognitive = careState.domains.find((d) => d.domain === "cognitive");
  if (!cognitive) return false;

  // Cognitive state should not be just diagnosis
  const cognitiveValue = cognitive.current_value?.toLowerCase() ?? "";
  return cognitiveValue.includes("dementia") &&
    !cognitiveValue.includes("memory") &&
    !cognitiveValue.includes("orientation");
}

/**
 * Check for medication state error
 */
function hasMedicationStateError(careState: CareState): boolean {
  const medication = careState.domains.find((d) => d.domain === "medication");
  if (!medication) return false;

  // Should distinguish prescribed vs administered
  const subdomains = medication.contextual_values.map((c) =>
    c.supporting_claim_ids[0]?.split("_")[2] || ""
  );
  const hasPrescribed = subdomains.some((s) => s.includes("prescribed"));
  const hasAdministered = subdomains.some((s) => s.includes("administered") || s.includes("taken"));

  return hasPrescribed && !hasAdministered; // Prescribed but no administration tracking
}

/**
 * Check for care network state error
 */
function hasCareNetworkStateError(careState: CareState): boolean {
  const careNetwork = careState.domains.find((d) => d.domain === "care_network");
  if (!careNetwork) return false;

  // Should have task owners identified
  return careNetwork.contextual_values.length > 0 &&
    careNetwork.contextual_values.every((c) =>
      !c.value.toLowerCase().includes("manage") &&
      !c.value.toLowerCase().includes("handle") &&
      !c.value.toLowerCase().includes("responsib")
    );
}

/**
 * Check for operational state error
 */
function hasOperationalStateError(careState: CareState): boolean {
  const operational = careState.domains.find((d) => d.domain === "operational");
  if (!operational) return false;

  // Should have open loops if evidence shows them
  return operational.contextual_values.length === 0;
}

/**
 * Check for baseline loss
 */
function hasBaselineLoss(careState: CareState): boolean {
  return careState.domains.some((d) =>
    d.trajectory.length > 0 &&
    !d.trajectory.some((t) => t.temporal_status === "baseline")
  );
}

/**
 * Check for intermediate state loss
 */
function hasIntermediateStateLoss(careState: CareState): boolean {
  return careState.domains.some((d) =>
    d.trajectory.length >= 3 &&
    d.trajectory.filter((t) => t.temporal_status !== "current" && t.temporal_status !== "baseline").length === 0
  );
}

/**
 * Check for improvement loss
 */
function hasImprovementLoss(careState: CareState): boolean {
  // Check if trajectory shows improvement but current state doesn't reflect it
  return careState.domains.some((d) => {
    const trajectory = d.trajectory;
    const hasImprovement = trajectory.some((t) =>
      t.value.includes("improv") || t.value.includes("better") || t.value.includes("independ")
    );
    const currentShowsImprovement = d.current_value?.includes("improv") ||
      d.current_value?.includes("better") ||
      d.current_value?.includes("independ");

    return hasImprovement && !currentShowsImprovement;
  });
}

/**
 * Check if state derived from diagnosis
 */
function isStateFromDiagnosis(careState: CareState, claims: Claim[]): boolean {
  const hasDiagnosisClaim = claims.some((c) =>
    c.statement.toLowerCase().includes("dementia") ||
    c.statement.toLowerCase().includes("alzheimer") ||
    c.statement.toLowerCase().includes("diagnosis")
  );

  const domainsOnlyDiagnosis = careState.domains.every((d) =>
    !d.current_value ||
    d.current_value.toLowerCase().includes("dementia") ||
    d.current_value.toLowerCase().includes("alzheimer") ||
    d.current_value.toLowerCase().includes("diagnosis")
  );

  return hasDiagnosisClaim && domainsOnlyDiagnosis;
}

/**
 * Check if state from single event
 */
function isStateFromSingleEvent(careState: CareState, claims: Claim[]): boolean {
  const activeClaims = claims.filter((c) => c.status === "active");
  const uniqueEvents = new Set(activeClaims.map((c) => c.source_event_ids[0]));
  return uniqueEvents.size === 1 && careState.domains.length > 1;
}

/**
 * Check if state from single claim
 */
function isStateFromSingleClaim(careState: CareState): boolean {
  return careState.domains.some((d) =>
    d.contextual_values.length === 1 &&
    d.contextual_values[0].supporting_claim_ids.length === 1 &&
    careState.domains.filter((d2) => d2.current_value).length > 1
  );
}

/**
 * Check for stale reconstruction
 */
function isStaleReconstruction(careState: CareState): boolean {
  const reconstructedAt = new Date(careState.reconstructed_at).getTime();
  const now = Date.now();
  return (now - reconstructedAt) > 24 * 60 * 60 * 1000; // Older than 24 hours
}

/**
 * Check for unsupported current state
 */
function hasUnsupportedCurrentState(careState: CareState): boolean {
  return careState.domains.some((d) =>
    d.current_value &&
    (d.confidence === "insufficient_evidence" || d.confidence === "weakly_supported") &&
    d.uncertainty !== "high" && d.uncertainty !== "unknown"
  );
}

/**
 * Check for provenance loss
 */
function hasProvenanceLoss(careState: CareState): boolean {
  return careState.traceability.some((t) =>
    t.claim_ids.length === 0 || t.event_ids.length === 0 || t.provenance_records.length === 0
  );
}

/**
 * Check for temporal error
 */
function hasTemporalError(careState: CareState): boolean {
  return careState.domains.some((d) =>
    d.trajectory.some((t) => {
      const claimTime = new Date(t.timestamp).getTime();
      const asOfTime = new Date(careState.as_of).getTime();
      return claimTime > asOfTime; // Future claim in past trajectory
    })
  );
}

/**
 * Check for dependency failure
 */
function hasDependencyFailure(careState: CareState): boolean {
  // Check if dependent domains (e.g., medication adherence depends on cognitive)
  const cognitive = careState.domains.find((d) => d.domain === "cognitive");
  const medication = careState.domains.find((d) => d.domain === "medication");

  if (cognitive && medication) {
    const cognitiveImpaired = Boolean(cognitive.current_value?.includes("impair") || cognitive.current_value?.includes("confus"));
    const adherenceCertain = Boolean(medication.current_value?.includes("adherent") || medication.current_value?.includes("taken as prescribed"));

    return cognitiveImpaired && adherenceCertain; // Cognitive impairment but perfect adherence claimed
  }

  return false;
}

/**
 * Check for mixed context collapse
 */
function hasMixedContextCollapse(careState: CareState): boolean {
  return careState.domains.some((d) =>
    d.contextual_values.length > 1 &&
    d.contextual_values.some((c) => c.context.location === "home") &&
    d.contextual_values.some((c) => c.context.location === "outdoors") &&
    d.contextual_values.every((c) => c.value === d.contextual_values[0].value)
  );
}

/**
 * Run validation on reconstruction result
 */
export function validateReconstructionResult(result: ReconstructionResult): {
  valid: boolean;
  failures: string[];
  warnings: string[];
} {
  const failures = validateCareState(result.care_state, Array.from(result.evidence_graph.claim_index.values()), []);

  // Check for additional warnings
  const warnings: string[] = [];

  if (result.reconstruction_metadata.traceability_completeness < 0.8) {
    warnings.push("Low traceability completeness");
  }

  if (result.reconstruction_metadata.domains_reconstructed < 4) {
    warnings.push("Fewer than 4 domains reconstructed");
  }

  if (result.care_state.open_loops.length === 0 && result.care_state.domains.some((d) => d.uncertainty === "high")) {
    warnings.push("High uncertainty domains but no open loops generated");
  }

  return {
    valid: failures.length === 0,
    failures,
    warnings,
  };
}

/**
 * Format validation report
 */
export function formatValidationReport(validation: ReturnType<typeof validateReconstructionResult>): string {
  let report = "=== STATE RECONSTRUCTION VALIDATION ===\n\n";

  if (validation.valid) {
    report += "✓ VALID - No failure modes detected\n\n";
  } else {
    report += `✗ INVALID - ${validation.failures.length} failure(s) detected:\n\n`;
    for (const failure of validation.failures) {
      report += `  • ${failure}\n`;
    }
    report += "\n";
  }

  if (validation.warnings.length > 0) {
    report += "Warnings:\n";
    for (const warning of validation.warnings) {
      report += `  ⚠ ${warning}\n`;
    }
  }

  return report;
}