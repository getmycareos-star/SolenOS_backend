/**
 * Contradiction & Conflict Intelligence — SolenOS Core Foundation
 *
 * IRREDUCIBLE PRINCIPLE:
 *   Conflict must not be destroyed by normalization.
 *   If evidence conflicts, SolenOS must preserve the conflict until there is
 *   a defensible basis for resolving it.
 *
 * This module does NOT:
 *   - redesign Evidence & Input Intelligence
 *   - redesign Temporal Intelligence
 *   - redesign Longitudinal Care State
 *   - perform clinical diagnosis
 *   - recommend treatments
 *   - manage tasks or notifications
 *
 * This module ONLY:
 *   - detects disagreement between claims
 *   - distinguishes difference from contradiction
 *   - preserves competing assertions
 *   - evaluates supporting evidence
 *   - represents unresolved conflicts
 *   - explains conflicts in terms of actual competing claims
 *   - applies explicit, defensible resolution rules
 *   - retains historical provenance of resolved conflicts
 */

export const CONFLICT_INTELLIGENCE_IDENTITY =
  "SolenOS never overwrites reality. It preserves disagreement until evidence resolves it.";

export const CONFLICT_INTELLIGENCE_DEFINING_PRINCIPLE =
  "A clean answer is not more valuable than a truthful representation of uncertainty and disagreement.";

export const NO_SILENT_RESOLUTION_POLICY =
  "SolenOS must not silently resolve a conflict by selecting the newest, most authoritative, majority, or highest-confidence claim without an explicit, defensible resolution rule. Competing assertions must remain traceable.";

export const COMPATIBILITY_STATUS = ["compatible", "apparent_conflict", "genuine_conflict"] as const;

export const CONFLICT_RESOLUTION_STATUS = [
  "unresolved",
  "provisionally_resolved",
  "resolved",
  "superseded",
  "invalidated",
] as const;

export const CONFLICT_TYPES = [
  "state",
  "identity",
  "temporal",
  "event",
  "diagnostic",
  "attribution",
  "outcome",
  "quantitative",
  "subjective",
] as const;

export const EVIDENCE_DERIVATION = [
  "direct_observation",
  "first_hand_report",
  "second_hand_report",
  "extracted_document_statement",
  "inferred_claim",
] as const;

export const SOURCE_LINEAGE_RELATIONSHIP = [
  "independent",
  "derived_from",
  "copied_from",
  "extracted_from_same_document",
] as const;

export const RESOLUTION_MECHANISMS = [
  "temporal_clarification",
  "explicit_correction",
  "direct_observation",
  "corroboration",
  "additional_evidence",
  "source_reconciliation",
  "user_confirmation",
  "state_transition_identified",
  "specificity_reconciled",
] as const;

export const CONTRADICTION_DETECTION_RULES = [
  "difference_is_not_contradiction",
  "temporal_change_is_not_contradiction",
  "uncertainty_is_not_contradiction",
  "subjectivity_is_not_automatic_contradiction",
  "specificity_difference_is_not_contradiction",
  "document_date_is_not_event_date",
  "source_authority_is_not_truth",
  "majority_vote_is_not_truth",
  "recency_is_not_truth",
  "duplicate_evidence_is_not_independent_corroboration",
  "preserve_all_competing_claims",
  "no_silent_overwrite",
  "resolution_must_preserve_history",
] as const;
