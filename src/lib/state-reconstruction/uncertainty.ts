/**
 * State Reconstruction — Uncertainty Propagator
 * SoT: docs/02-product/solenos-state-reconstruction.md
 */

import type {
  Claim,
  DomainState,
  ContextualState,
  UncertaintyLevel,
  ReconstructionConfidence,
  EvidenceSummary,
} from "./types";

import { UNCERTAINTY_LEVELS, RECONSTRUCTION_CONFIDENCE } from "./contract-constants";

/**
 * Propagate uncertainty from claims to domain state
 */
export function propagateUncertainty(
  claims: Claim[],
  domain: string,
  subdomain: string
): {
  uncertainty: UncertaintyLevel;
  uncertainty_narrative: string;
  confidence: ReconstructionConfidence;
  evidence_summary: EvidenceSummary;
} {
  const relevantClaims = claims.filter(
    (c) => c.domain === domain && c.subdomain === subdomain && c.status === "active"
  );

  if (relevantClaims.length === 0) {
    return {
      uncertainty: "unknown",
      uncertainty_narrative: "No evidence available for this domain.",
      confidence: "insufficient_evidence",
      evidence_summary: createEmptyEvidenceSummary(),
    };
  }

  // Aggregate uncertainty levels
  const uncertaintyLevels = relevantClaims.map((c) => c.uncertainty);
  const aggregatedUncertainty = aggregateUncertainty(uncertaintyLevels);

  // Build uncertainty narrative
  const uncertaintyNarrative = buildUncertaintyNarrative(relevantClaims, aggregatedUncertainty);

  // Calculate confidence
  const confidence = calculateReconstructionConfidence(relevantClaims, aggregatedUncertainty);

  // Build evidence summary
  const evidenceSummary = buildEvidenceSummary(relevantClaims);

  return {
    uncertainty: aggregatedUncertainty,
    uncertainty_narrative: uncertaintyNarrative,
    confidence,
    evidence_summary: evidenceSummary,
  };
}

/**
 * Aggregate multiple uncertainty levels
 */
function aggregateUncertainty(levels: UncertaintyLevel[]): UncertaintyLevel {
  const order = { none: 0, low: 1, medium: 2, high: 3, unknown: 4 };

  // If any unknown, result is unknown
  if (levels.includes("unknown")) return "unknown";

  // If any high, result is high
  if (levels.includes("high")) return "high";

  // If majority medium, result is medium
  const mediumCount = levels.filter((l) => l === "medium").length;
  if (mediumCount >= levels.length / 2) return "medium";

  // If any low, result is low
  if (levels.includes("low")) return "low";

  return "none";
}

/**
 * Build human-readable uncertainty narrative
 */
function buildUncertaintyNarrative(claims: Claim[], aggregated: UncertaintyLevel): string {
  const parts: string[] = [];

  // Count uncertainty reasons
  const reasonCounts = new Map<string, number>();
  for (const claim of claims) {
    if (claim.uncertainty_reason) {
      reasonCounts.set(claim.uncertainty_reason, (reasonCounts.get(claim.uncertainty_reason) || 0) + 1);
    }
  }

  // Describe uncertainty sources
  if (reasonCounts.size > 0) {
    const topReasons = Array.from(reasonCounts.entries())
      .sort((a, b) => b[1] - a[1])
      .slice(0, 3)
      .map(([reason, count]) => `${reason} (${count} claim${count > 1 ? "s" : ""})`);
    parts.push(`Uncertainty sources: ${topReasons.join("; ")}.`);
  }

  // Describe evidence gaps
  const temporalStatuses = claims.map((c) => c.temporal_status);
  const hasCurrent = temporalStatuses.includes("current");
  const hasRecent = temporalStatuses.includes("recent");
  const onlyHistorical = temporalStatuses.every((s) => s === "historical" || s === "baseline");

  if (onlyHistorical) {
    parts.push("All evidence is historical; current state not directly observed.");
  } else if (!hasCurrent && hasRecent) {
    parts.push("Most recent evidence is from recent period; current status inferred.");
  } else if (!hasCurrent && !hasRecent) {
    parts.push("No recent evidence; state reconstructed from historical trajectory.");
  }

  // Describe claim diversity
  const sources = [...new Set(claims.map((c) => c.evidence_weight))];
  if (sources.length === 1) {
    parts.push(`Single evidence source type: ${sources[0]}.`);
  } else {
    parts.push(`Multiple evidence sources: ${sources.join(", ")}.`);
  }

  // Describe contradictions
  const contradicted = claims.filter((c) => c.status === "contradicted").length;
  if (contradicted > 0) {
    parts.push(`${contradicted} contradicted claim${contradicted > 1 ? "s" : ""} excluded from current state.`);
  }

  // Describe supersession
  const superseded = claims.filter((c) => c.status === "superseded").length;
  if (superseded > 0) {
    parts.push(`${superseded} prior claim${superseded > 1 ? "s" : ""} superseded by newer evidence.`);
  }

  // Overall assessment
  const overall = getUncertaintyDescription(aggregated);
  parts.unshift(`Overall uncertainty: ${overall}.`);

  return parts.join(" ");
}

/**
 * Get description for uncertainty level
 */
function getUncertaintyDescription(level: UncertaintyLevel): string {
  const descriptions: Record<UncertaintyLevel, string> = {
    none: "well-established with consistent evidence",
    low: "minor uncertainty in specific details",
    medium: "moderate uncertainty; key aspects not fully established",
    high: "significant uncertainty; current state not reliably determined",
    unknown: "insufficient evidence to assess",
  };
  return descriptions[level];
}

/**
 * Calculate reconstruction confidence
 */
function calculateReconstructionConfidence(
  claims: Claim[],
  uncertainty: UncertaintyLevel
): ReconstructionConfidence {
  const activeClaims = claims.filter((c) => c.status === "active");
  if (activeClaims.length === 0) return "insufficient_evidence";

  // Base confidence from evidence weights
  const weightScores: Record<string, number> = {
    clinical_assessment: 0.9,
    caregiver_observation: 0.7,
    patient_self_report: 0.6,
    device_data: 0.85,
    historical_record: 0.5,
    indirect_inference: 0.3,
  };

  const avgWeight = activeClaims.reduce((sum, c) => sum + (weightScores[c.evidence_weight] || 0.5), 0) / activeClaims.length;

  // Adjust for uncertainty
  const uncPenalty: Record<UncertaintyLevel, number> = {
    none: 0,
    low: 0.1,
    medium: 0.25,
    high: 0.5,
    unknown: 0.8,
  };

  const adjusted = avgWeight - uncPenalty[uncertainty];

  // Adjust for claim count
  const countBonus = Math.min(0.15, activeClaims.length * 0.03);

  const final = Math.max(0, Math.min(1, adjusted + countBonus));

  if (final >= 0.75) return "well_supported";
  if (final >= 0.55) return "moderately_supported";
  if (final >= 0.35) return "weakly_supported";
  if (final > 0) return "insufficient_evidence";
  return "contradicted";
}

/**
 * Build evidence summary
 */
function buildEvidenceSummary(claims: Claim[]): EvidenceSummary {
  const active = claims.filter((c) => c.status === "active");
  const superseded = claims.filter((c) => c.status === "superseded");
  const contradicted = claims.filter((c) => c.status === "contradicted");
  const uncertain = claims.filter((c) => c.uncertainty === "high" || c.uncertainty === "unknown");

  const timestamps = claims.map((c) => new Date(c.valid_from).getTime());
  const earliest = timestamps.length > 0 ? new Date(Math.min(...timestamps)).toISOString() : null;
  const latest = timestamps.length > 0 ? new Date(Math.max(...timestamps)).toISOString() : null;

  // Find gaps
  const sortedTimes = timestamps.sort((a, b) => a - b);
  const gaps: string[] = [];
  for (let i = 1; i < sortedTimes.length; i++) {
    const diffDays = (sortedTimes[i] - sortedTimes[i - 1]) / (1000 * 60 * 60 * 24);
    if (diffDays > 60) {
      gaps.push(`${Math.round(diffDays)} days between ${new Date(sortedTimes[i - 1]).toISOString().split("T")[0]} and ${new Date(sortedTimes[i]).toISOString().split("T")[0]}`);
    }
  }

  return {
    total_claims: claims.length,
    active_claims: active.length,
    superseded_claims: superseded.length,
    contradicted_claims: contradicted.length,
    uncertain_claims: uncertain.length,
    source_diversity: [...new Set(claims.map((c) => c.evidence_weight))],
    earliest_evidence: earliest,
    latest_evidence: latest,
    gap_periods: gaps,
  };
}

/**
 * Create empty evidence summary
 */
function createEmptyEvidenceSummary(): EvidenceSummary {
  return {
    total_claims: 0,
    active_claims: 0,
    superseded_claims: 0,
    contradicted_claims: 0,
    uncertain_claims: 0,
    source_diversity: [],
    earliest_evidence: null,
    latest_evidence: null,
    gap_periods: [],
  };
}

/**
 * Propagate uncertainty to contextual states
 */
export function propagateContextualUncertainty(
  contextualStates: ContextualState[],
  claims: Claim[]
): ContextualState[] {
  return contextualStates.map((ctx) => {
    const supportingClaims = claims.filter((c) => ctx.supporting_claim_ids.includes(c.id));
    const contradictingClaims = claims.filter((c) => ctx.contradicting_claim_ids.includes(c.id));

    if (supportingClaims.length === 0) {
      return { ...ctx, uncertainty: "unknown" };
    }

    const uncertaintyLevels = supportingClaims.map((c) => c.uncertainty);
    const aggregated = aggregateUncertainty(uncertaintyLevels);

    // Increase uncertainty if there are contradicting claims
    let finalUncertainty = aggregated;
    if (contradictingClaims.length > 0) {
      const order = { none: 0, low: 1, medium: 2, high: 3, unknown: 4 };
      finalUncertainty = UNCERTAINTY_LEVELS[Math.min(4, order[aggregated] + 1)] as UncertaintyLevel;
    }

    return { ...ctx, uncertainty: finalUncertainty };
  });
}

/**
 * Determine if domain state is stable
 */
export function assessStability(
  claims: Claim[],
  trajectory: Array<{ timestamp: string; value: string }>
): boolean {
  if (trajectory.length < 2) return false;

  // Check recent trajectory for changes
  const recent = trajectory.slice(-3);
  const values = recent.map((t) => t.value);
  const allSame = values.every((v) => v === values[0]);

  // Check for recent supersession
  const recentClaims = claims.filter((c) => {
    const claimTime = new Date(c.valid_from).getTime();
    const now = Date.now();
    return (now - claimTime) / (1000 * 60 * 60 * 24) < 30; // last 30 days
  });

  const hasRecentSupersession = recentClaims.some((c) => c.status === "superseded");
  const hasRecentContradiction = recentClaims.some((c) => c.status === "contradicted");

  return allSame && !hasRecentSupersession && !hasRecentContradiction;
}

/**
 * Format uncertainty for caregiver-facing output
 */
export function formatUncertaintyForOutput(
  uncertainty: UncertaintyLevel,
  narrative: string
): string {
  const prefixes: Record<UncertaintyLevel, string> = {
    none: "Well-established:",
    low: "Minor uncertainty:",
    medium: "Moderate uncertainty:",
    high: "Significant uncertainty:",
    unknown: "Unknown:",
  };

  return `${prefixes[uncertainty]} ${narrative}`;
}