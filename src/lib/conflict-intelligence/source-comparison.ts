import type { ClaimSource, ConflictClaim, SourceLineage } from "./types";

export type SourceComparisonResult = {
  independent_claims: ConflictClaim[];
  dependent_claims: ConflictClaim[];
  duplicate_groups: ConflictClaim[][];
  corroboration_quality: "none" | "weak" | "moderate" | "strong";
  warnings: string[];
};

export function compareSources(claims: ConflictClaim[]): SourceComparisonResult {
  const warnings: string[] = [];
  const independent: ConflictClaim[] = [];
  const dependent: ConflictClaim[] = [];
  const duplicateGroups: ConflictClaim[][] = [];

  const seen = new Set<string>();

  for (const claim of claims) {
    if (seen.has(claim.claim_id)) continue;

    if (claim.source.lineage && claim.source.lineage.relationship !== "independent") {
      dependent.push(claim);
      warnings.push(
        `Claim ${claim.claim_id} derives from source ${claim.source.lineage.derived_from_source_id}. Not independent.`,
      );
      seen.add(claim.claim_id);
      continue;
    }

    const group = [claim];
    seen.add(claim.claim_id);

    for (const other of claims) {
      if (other.claim_id === claim.claim_id) continue;
      if (seen.has(other.claim_id)) continue;

      if (areDuplicateSources(claim.source, other.source)) {
        group.push(other);
        seen.add(other.claim_id);
        warnings.push(
          `Claims ${claim.claim_id} and ${other.claim_id} share source lineage. Not independent corroboration.`,
        );
      }
    }

    if (group.length > 1) {
      duplicateGroups.push(group);
    }

    independent.push(claim);
  }

  const ungrouped = claims.filter((c) => !seen.has(c.claim_id));
  for (const claim of ungrouped) {
    independent.push(claim);
  }

  let corroborationQuality: SourceComparisonResult["corroboration_quality"] = "none";
  const independentCount = independent.length;
  if (independentCount >= 3) corroborationQuality = "strong";
  else if (independentCount === 2) corroborationQuality = "moderate";
  else if (independentCount === 1) corroborationQuality = "weak";

  return {
    independent_claims: independent,
    dependent_claims: dependent,
    duplicate_groups: duplicateGroups,
    corroboration_quality: corroborationQuality,
    warnings,
  };
}

function areDuplicateSources(a: ClaimSource, b: ClaimSource): boolean {
  if (a.source_id === b.source_id) return true;
  if (a.document_id && b.document_id && a.document_id === b.document_id) return true;
  if (a.raw_input_id && b.raw_input_id && a.raw_input_id === b.raw_input_id) return true;
  if (a.lineage && b.lineage && a.lineage.derived_from_source_id && b.lineage.derived_from_source_id) {
    if (a.lineage.derived_from_source_id === b.lineage.derived_from_source_id) return true;
  }
  return false;
}

export function createSourceLineage(
  relationship: SourceLineage["relationship"],
  derivedFromSourceId: string | null,
  description: string | null,
): SourceLineage {
  return {
    relationship,
    derived_from_source_id: derivedFromSourceId,
    derivation_description: description,
  };
}

export function createClaimSource(
  sourceType: ClaimSource["source_type"],
  sourceLabel: string,
  sourceId: string,
  caregiverId: string | null,
  documentId: string | null,
  rawInputId: string | null,
  lineage: SourceLineage | null,
): ClaimSource {
  return {
    source_type: sourceType,
    source_label: sourceLabel,
    source_id: sourceId,
    caregiver_id: caregiverId,
    document_id: documentId,
    raw_input_id: rawInputId,
    lineage,
  };
}
