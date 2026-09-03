import type {
  CompatibilityStatusValue,
  ConflictClaim,
  TemporalAssertion,
  CompatibilityInput,
  CompatibilityOutput,
} from "./types";

function isTemporalChange(
  a: TemporalAssertion | null,
  b: TemporalAssertion | null,
): boolean {
  if (!a || !b) return false;
  if (a.kind === "unknown" || b.kind === "unknown") return false;
  if (a.kind !== b.kind) return false;
  if (a.value && b.value && a.value !== b.value) {
    return true;
  }
  if (a.is_range && b.is_range && a.range_start && a.range_end && b.range_start && b.range_end) {
    const aEnd = new Date(a.range_end).getTime();
    const bStart = new Date(b.range_start).getTime();
    if (aEnd < bStart) return true;
    const bEnd = new Date(b.range_end).getTime();
    const aStart = new Date(a.range_start).getTime();
    if (bEnd < aStart) return true;
  }
  return false;
}

function isSpecificityDifference(a: ConflictClaim, b: ConflictClaim): boolean {
  const aIsSpecific = a.object.length > 0 && a.object !== "unspecified";
  const bIsSpecific = b.object.length > 0 && b.object !== "unspecified";
  if (!aIsSpecific || !bIsSpecific) return true;
  if (a.predicate === b.predicate && a.subject === b.subject) {
    if (a.object !== b.object) {
      const aIsSubset = b.object.toLowerCase().includes(a.object.toLowerCase());
      const bIsSubset = a.object.toLowerCase().includes(b.object.toLowerCase());
      if (aIsSubset || bIsSubset) return true;
      const specificityPronePredicates = ["has_diagnosis", "diagnosis", "condition", "disease", "disorder"];
      if (specificityPronePredicates.some((p) => a.predicate.includes(p) || b.predicate.includes(p))) {
        return true;
      }
    }
  }
  return false;
}

function isSubjectivePerspective(a: ConflictClaim, b: ConflictClaim): boolean {
  const subjectivePredicates = [
    "feels",
    "is_doing_well",
    "is_struggling",
    "appears",
    "seems",
    "reports_feeling",
    "observes",
    "thinks",
    "believes",
  ];
  const aIsSubjective = subjectivePredicates.some((p) => a.predicate.includes(p));
  const bIsSubjective = subjectivePredicates.some((p) => b.predicate.includes(p));
  if (aIsSubjective && bIsSubjective) {
    if (a.predicate === b.predicate && a.subject === b.subject) {
      return true;
    }
  }
  return false;
}

function claimsAreIdentical(a: ConflictClaim, b: ConflictClaim): boolean {
  return (
    a.subject === b.subject &&
    a.predicate === b.predicate &&
    a.object === b.object &&
    a.raw_text === b.raw_text
  );
}

export function analyzeCompatibility(
  input: CompatibilityInput,
): CompatibilityOutput {
  const { claim_a, claim_b, temporal_context } = input;

  if (claimsAreIdentical(claim_a, claim_b)) {
    return {
      status: "compatible",
      analysis: "Claims are identical.",
      blocking_factors: [],
      reconciling_factors: ["identical_text", "same_subject_predicate_object"],
    };
  }

  const blocking: string[] = [];
  const reconciling: string[] = [];

  if (isTemporalChange(claim_a.temporal_assertion, claim_b.temporal_assertion)) {
    reconciling.push("temporal_change");
  }

  if (isSpecificityDifference(claim_a, claim_b)) {
    reconciling.push("specificity_difference");
  }

  if (isSubjectivePerspective(claim_a, claim_b)) {
    reconciling.push("subjective_perspective");
  }

  if (claim_a.subject === claim_b.subject && claim_a.predicate === claim_b.predicate) {
    if (claim_a.object !== claim_b.object) {
      blocking.push("same_subject_predicate_different_object");
    }
  }

  if (claim_a.evidence_derivation === "direct_observation" && claim_b.evidence_derivation === "inferred_claim") {
    reconciling.push("direct_observation_vs_inference");
  }

  if (claim_a.source.source_type === "document" && claim_b.source.source_type === "caregiver") {
    reconciling.push("document_vs_caregiver_perspective");
  }

  const hasBlocking = blocking.length > 0 && reconciling.length === 0;

  if (hasBlocking) {
    return {
      status: "genuine_conflict",
      analysis: `Claims are mutually incompatible: ${blocking.join(", ")}.`,
      blocking_factors: blocking,
      reconciling_factors: [],
    };
  }

  if (blocking.length > 0 && reconciling.length > 0) {
    return {
      status: "apparent_conflict",
      analysis: `Claims appear incompatible but have reconciling context: ${reconciling.join(", ")}.`,
      blocking_factors: blocking,
      reconciling_factors: reconciling,
    };
  }

  if (reconciling.length > 0) {
    return {
      status: "compatible",
      analysis: `Claims are compatible given: ${reconciling.join(", ")}.`,
      blocking_factors: [],
      reconciling_factors: reconciling,
    };
  }

  return {
    status: "apparent_conflict",
    analysis: "Claims differ but compatibility cannot be determined without additional context.",
    blocking_factors: ["insufficient_context"],
    reconciling_factors: [],
  };
}
