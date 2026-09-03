import type { ConflictClaim, ConflictObject } from "./types";

/**
 * Conflict Explanation — generates human-readable and machine-queryable
 * explanations of what conflicts and why.
 *
 * Every material conflict must answer:
 *   - What exactly conflicts?
 *   - Why does SolenOS believe these claims conflict?
 */

export type ConflictExplanation = {
  summary: string;
  detailed: string;
  competing_claims: Array<{
    claim_id: string;
    source_label: string;
    source_type: string;
    raw_text: string;
    temporal_assertion: string | null;
    evidence_derivation: string;
  }>;
  compatibility_status: string;
  resolution_status: string;
  why_conflicting: string;
  temporal_notes: string;
  source_notes: string;
  resolution_path: string | null;
};

export function generateConflictExplanation(conflict: ConflictObject): ConflictExplanation {
  const claims = conflict.claims;
  const claimDescriptions = claims.map((c) => ({
    claim_id: c.claim_id,
    source_label: c.source.source_label,
    source_type: c.source.source_type,
    raw_text: c.raw_text,
    temporal_assertion: c.temporal_assertion?.value ?? null,
    evidence_derivation: c.evidence_derivation,
  }));

  const whyConflicting = buildWhyConflicting(conflict);
  const temporalNotes = buildTemporalNotes(conflict);
  const sourceNotes = buildSourceNotes(conflict);
  const resolutionPath = buildResolutionPath(conflict);

  const summary = buildSummary(conflict);
  const detailed = buildDetailedExplanation(conflict, whyConflicting, temporalNotes, sourceNotes, resolutionPath);

  return {
    summary,
    detailed,
    competing_claims: claimDescriptions,
    compatibility_status: conflict.compatibility_status,
    resolution_status: conflict.resolution_status,
    why_conflicting: whyConflicting,
    temporal_notes: temporalNotes,
    source_notes: sourceNotes,
    resolution_path: resolutionPath,
  };
}

function buildSummary(conflict: ConflictObject): string {
  if (conflict.claims.length < 2) {
    return "Insufficient claims to describe conflict.";
  }

  const claimA = conflict.claims[0]!;
  const claimB = conflict.claims[1]!;

  if (conflict.compatibility_status === "compatible") {
    return `Claims about "${claimA.subject}" appear compatible and do not conflict.`;
  }

  if (conflict.compatibility_status === "apparent_conflict") {
    return `Claims about "${claimA.subject}" appear to conflict but may be reconcilable: ${claimA.raw_text.slice(0, 100)} vs ${claimB.raw_text.slice(0, 100)}.`;
  }

  return `Conflicting evidence about "${claimA.subject}": ${claimA.raw_text.slice(0, 100)} versus ${claimB.raw_text.slice(0, 100)}.`;
}

function buildDetailedExplanation(
  conflict: ConflictObject,
  whyConflicting: string,
  temporalNotes: string,
  sourceNotes: string,
  resolutionPath: string | null,
): string {
  const parts = [
    `CONFLICT: ${conflict.conflict_id}`,
    `TYPE: ${conflict.conflict_type}`,
    `STATUS: ${conflict.compatibility_status} / ${conflict.resolution_status}`,
    ``,
    `COMPETING CLAIMS:`,
    ...conflict.claims.map(
      (c, i) =>
        `  [${i + 1}] ${c.source.source_label} (${c.source.source_type}): "${c.raw_text}"` +
        (c.temporal_assertion?.value ? ` [${c.temporal_assertion.kind}: ${c.temporal_assertion.value}]` : "") +
        ` [${c.evidence_derivation}]`,
    ),
    ``,
    `WHY CONFLICTING:`,
    `  ${whyConflicting}`,
    ``,
  ];

  if (temporalNotes) {
    parts.push(`TEMPORAL CONTEXT:`, `  ${temporalNotes}`, ``);
  }

  if (sourceNotes) {
    parts.push(`SOURCE NOTES:`, `  ${sourceNotes}`, ``);
  }

  if (resolutionPath) {
    parts.push(`RESOLUTION PATH:`, `  ${resolutionPath}`, ``);
  } else {
    parts.push(`RESOLUTION:`, `  Conflict remains unresolved. The available evidence does not establish which claim is current.`, ``);
  }

  return parts.join("\n");
}

function buildWhyConflicting(conflict: ConflictObject): string {
  if (conflict.compatibility_status === "compatible") {
    return "Claims are compatible and do not genuinely conflict.";
  }

  const subjects = conflict.claims.map((c) => `${c.predicate}:${c.object}`).join("; ");
  return `Two or more sources make incompatible assertions about the same subject: ${subjects}. The available evidence does not establish which claim is current.`;
}

function buildTemporalNotes(conflict: ConflictObject): string {
  const temporalClaims = conflict.claims.filter((c) => c.temporal_assertion !== null);
  if (temporalClaims.length === 0) {
    return "No temporal assertions found in competing claims.";
  }

  const parts = temporalClaims.map((c) => {
    const t = c.temporal_assertion!;
    return `- ${c.source.source_label}: ${t.kind} ${t.value ?? "unknown"}`;
  });

  const scope = conflict.temporal_scope;
  if (scope) {
    parts.push(`- Conflict temporal scope: ${scope.kind} ${scope.value ?? "unknown"} (confidence: ${scope.confidence})`);
  }

  return parts.join("\n");
}

function buildSourceNotes(conflict: ConflictObject): string {
  const parts: string[] = [];

  const hasMixedSources = conflict.claims.some((c) => c.source.source_type === "caregiver") &&
    conflict.claims.some((c) => c.source.source_type === "document");

  if (hasMixedSources) {
    parts.push("Claims come from mixed source types (caregiver and document). This does not automatically make one more credible.");
  }

  const hasDerived = conflict.claims.some((c) => c.source.lineage && c.source.lineage.relationship !== "independent");
  if (hasDerived) {
    parts.push("At least one claim derives from another source. Derived claims are not independent corroboration.");
  }

  if (parts.length === 0) {
    return "All claims are from independent sources with no known lineage.";
  }

  return parts.join(" ");
}

function buildResolutionPath(conflict: ConflictObject): string | null {
  if (conflict.resolution_status === "unresolved") {
    return null;
  }

  if (conflict.resolution_status === "resolved" && conflict.resolution_evidence) {
    const evidenceClaims = conflict.resolution_evidence.evidence_claim_ids
      .map((id) => conflict.claims.find((c) => c.claim_id === id))
      .filter(Boolean);
    const evidenceLabels = evidenceClaims.map((c) => c!.source.source_label).join(", ");

    return `Resolved via ${conflict.resolution_evidence.mechanism} (${conflict.resolution_evidence.resolved_by}) using evidence from: ${evidenceLabels}. Description: ${conflict.resolution_evidence.description}`;
  }

  if (conflict.resolution_status === "provisionally_resolved") {
    return `Provisionally resolved via ${conflict.resolution_mechanism ?? "unknown mechanism"}. Awaiting confirmation.`;
  }

  if (conflict.resolution_status === "superseded") {
    return "Conflict was superseded by a later correction or state transition.";
  }

  if (conflict.resolution_status === "invalidated") {
    return "Conflict was invalidated.";
  }

  return null;
}
