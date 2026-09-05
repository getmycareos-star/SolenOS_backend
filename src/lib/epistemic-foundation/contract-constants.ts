export const EPISTEMIC_FOUNDATION_IDENTITY = "epistemic-foundation";
export const EPISTEMIC_FOUNDATION_DEFINING_PRINCIPLE =
  "Strictly separate what the evidence says, what SolenOS can establish, what SolenOS infers, and what SolenOS does not know.";

export const EVIDENCE_KINDS = [
  "observed_measurement",
  "clinician_documentation",
  "patient_report",
  "caregiver_report",
  "third_party_report",
  "historical_record",
  "contradictory_evidence",
] as const;
export type EvidenceKind = (typeof EVIDENCE_KINDS)[number];

export const EPISTEMIC_STATUSES = [
  "direct",
  "reported",
  "documented",
  "derived",
  "inferred",
  "assumed",
  "unknown",
  "unsupported",
  "contradicted",
] as const;
export type EpistemicStatus = (typeof EPISTEMIC_STATUSES)[number];

export const CLAIM_LIFECYCLE_STATUSES = [
  "proposed",
  "supported",
  "established",
  "uncertain",
  "contradicted",
  "superseded",
  "rejected",
  "unknown",
] as const;
export type ClaimLifecycleStatus = (typeof CLAIM_LIFECYCLE_STATUSES)[number];

export const INFERENCE_CONFIDENCE_DIMENSIONS = [
  "extraction_correctness",
  "evidence_reliability",
  "inference_validity",
] as const;
export type InferenceConfidenceDimension = (typeof INFERENCE_CONFIDENCE_DIMENSIONS)[number];

export const UNCERTAINTY_LEVELS = [
  "known",
  "reported",
  "probable",
  "possible",
  "suspected",
  "uncertain",
  "conflicting",
  "unknown",
  "unsupported",
] as const;
export type UncertaintyLevel = (typeof UNCERTAINTY_LEVELS)[number];

export const ABSENCE_MODELS = [
  "missing",
  "absent",
  "not_mentioned",
  "explicitly_denied",
  "explicitly_ruled_out",
  "not_assessed",
  "unknown",
] as const;
export type AbsenceModel = (typeof ABSENCE_MODELS)[number];

export const REASONING_TYPES = [
  "pattern_match",
  "trend_analysis",
  "causal_inference",
  "analogical_reasoning",
  "deduction",
  "abduction",
  "aggregation",
  "temporal_reasoning",
] as const;
export type ReasoningType = (typeof REASONING_TYPES)[number];

export const EPISTEMIC_RULES = [
  "EVIDENCE_IS_NOT_AUTOMATIC_TRUTH",
  "INFERENCE_CANNOT_SILENTLY_BECOME_FACT",
  "UNKNOWN_IS_NOT_FALSE",
  "ABSENCE_OF_EVIDENCE_IS_NOT_EVIDENCE_OF_ABSENCE",
  "EVERY_MATERIAL_CLAIM_MUST_HAVE_TRACEABLE_SUPPORT",
  "CONTRADICTORY_EVIDENCE_MUST_REMAIN_REPRESENTABLE",
  "CLAIM_MUST_PRESERVE_EPISTEMIC_STATUS",
  "CONFIDENCE_MUST_NOT_REPLACE_PROVENANCE",
  "GENERATED_EXPLANATION_IS_NOT_SUBSTITUTE_FOR_PROVENANCE_CHAIN",
  "SYSTEM_MUST_DISTINGUISH_SOURCE_STATEMENTS_FROM_CONCLUSIONS",
  "REPORTED_INFORMATION_MUST_NOT_MASQUERADE_AS_OBSERVED",
  "DERIVED_FACTS_MUST_NOT_MASQUERADE_AS_DIRECT",
  "SOURCE_AUTHORITY_MUST_NOT_BECOME_CERTAINTY",
  "STALE_KNOWLEDGE_MUST_NOT_BE_PRESENTED_AS_CURRENT",
  "ASSUMPTIONS_MUST_BE_PRESERVED_EXPLICITLY",
] as const;
export type EpistemicRule = (typeof EPISTEMIC_RULES)[number];

export const EPISTEMIC_VIOLATIONS = [
  "INFERENCE_AS_FACT",
  "EXTRACTION_AS_TRUTH",
  "UNSUPPORTED_CLAIM",
  "FABRICATED_EVIDENCE",
  "FABRICATED_PROVENANCE",
  "HIDDEN_ASSUMPTION",
  "CONFIDENCE_INFLATION",
  "UNCERTAINTY_COLLAPSE",
  "STATUS_COLLAPSE",
  "UNKNOWN_TO_FALSE",
  "REPORTED_TO_OBSERVED",
  "DERIVED_TO_DIRECT",
  "SOURCE_AUTHORITY_TO_CERTAINTY",
  "CONTRADICTION_SUPPRESSION",
  "STALE_KNOWLEDGE_AS_CURRENT",
  "REASONING_CHAIN_LOSS",
  "EVIDENCE_MISATTRIBUTION",
  "CLAIM_DETACHED_FROM_SOURCE",
  "HALLUCINATED_EXPLANATION",
] as const;
export type EpistemicViolation = (typeof EPISTEMIC_VIOLATIONS)[number];

export const FORMAL_RULES = [
  {
    id: "R1",
    statement: "Evidence is not automatically truth.",
    violation: "EVIDENCE_AS_TRUTH" as EpistemicViolation,
  },
  {
    id: "R2",
    statement: "An inference cannot silently become a fact.",
    violation: "INFERENCE_AS_FACT" as EpistemicViolation,
  },
  {
    id: "R3",
    statement: "Unknown is not false.",
    violation: "UNKNOWN_TO_FALSE" as EpistemicViolation,
  },
  {
    id: "R4",
    statement: "Absence of evidence is not evidence of absence.",
    violation: "ABSENCE_AS_ABSENCE" as EpistemicViolation,
  },
  {
    id: "R5",
    statement: "Every material claim must have traceable support.",
    violation: "UNSUPPORTED_CLAIM" as EpistemicViolation,
  },
  {
    id: "R6",
    statement: "Contradictory evidence must remain representable.",
    violation: "CONTRADICTION_SUPPRESSION" as EpistemicViolation,
  },
  {
    id: "R7",
    statement: "A claim must preserve its epistemic status.",
    violation: "STATUS_COLLAPSE" as EpistemicViolation,
  },
  {
    id: "R8",
    statement: "Confidence must not replace provenance.",
    violation: "CONFIDENCE_INFLATION" as EpistemicViolation,
  },
  {
    id: "R9",
    statement: "A generated explanation is not a substitute for an actual reasoning/provenance chain.",
    violation: "HALLUCINATED_EXPLANATION" as EpistemicViolation,
  },
  {
    id: "R10",
    statement: "The system must be able to distinguish what the source said from what SolenOS concluded.",
    violation: "SOURCE_CONCLUSION_CONFLATION" as EpistemicViolation,
  },
] as const;

export type FormalRule = (typeof FORMAL_RULES)[number];
