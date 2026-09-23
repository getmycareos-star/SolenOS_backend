/**
 * State Reconstruction — Contract Constants
 * SoT: docs/02-product/solenos-state-reconstruction.md
 */

export const STATE_RECONSTRUCTION_IDENTITY = "StateReconstruction";
export const STATE_RECONSTRUCTION_PURPOSE =
  "Reconstruct the best-supported current care state from the entire valid evidence history—not merely repeat the newest observation—and must preserve uncertainty, context, provenance, and historical state where they materially affect the reconstruction.";

export const STATE_RECONSTRUCTION_NOT = [
  "summarization of latest notes",
  "copying the most recent observation",
  "diagnosis-driven state inference",
  "single-source state derivation",
  "flattening context-specific states",
  "discarding superseded claims",
  "resolving contradictions by force",
  "inventing state where evidence is insufficient",
] as const;

export const CARE_STATE_DOMAINS = [
  "physical",
  "cognitive",
  "functional",
  "medication",
  "care_network",
  "operational",
] as const;

export const PHYSICAL_SUBDOMAINS = [
  "mobility",
  "strength",
  "pain",
  "breathing",
  "falls",
  "sleep",
  "appetite",
  "weight",
] as const;

export const COGNITIVE_SUBDOMAINS = [
  "memory",
  "orientation",
  "recognition",
  "language",
  "executive_function",
  "fluctuations",
] as const;

export const FUNCTIONAL_SUBDOMAINS = [
  "bathing",
  "dressing",
  "toileting",
  "feeding",
  "transfers",
  "medication_management",
  "finances",
  "cooking",
  "transportation",
] as const;

export const MEDICATION_SUBDOMAINS = [
  "prescribed",
  "filled",
  "available",
  "administered",
  "taken",
  "discontinued",
  "adherence_uncertainty",
] as const;

export const CARE_NETWORK_SUBDOMAINS = [
  "providers",
  "task_owners",
  "handoffs",
  "availability",
  "coordination_state",
] as const;

export const OPERATIONAL_SUBDOMAINS = [
  "appointments",
  "referrals",
  "pending_results",
  "decisions",
  "unresolved_questions",
  "open_loops",
] as const;

export const STATE_RECONSTRUCTION_STAGES = [
  "evidence_graph_construction",
  "entity_resolution",
  "claim_aggregation",
  "provenance_binding",
  "supersession_resolution",
  "temporal_validity_filtering",
  "uncertainty_propagation",
  "contradiction_preservation",
  "context_preservation",
  "domain_reconstruction",
  "state_assembly",
  "traceability_binding",
] as const;

export const CLAIM_STATUSES = [
  "active",
  "superseded",
  "contradicted",
  "uncertain",
  "invalidated",
  "historical",
] as const;

export const EVIDENCE_WEIGHTS = [
  "clinical_assessment",
  "caregiver_observation",
  "patient_self_report",
  "device_data",
  "historical_record",
  "indirect_inference",
] as const;

export const UNCERTAINTY_LEVELS = [
  "none",
  "low",
  "medium",
  "high",
  "unknown",
] as const;

export const CONTRADICTION_TYPES = [
  "source_disagreement",
  "context_mismatch",
  "temporal_conflict",
  "definition_mismatch",
  "measurement_variance",
] as const;

export const SUPERSESSION_RELATIONS = [
  "direct_replacement",
  "refinement",
  "correction",
  "expansion",
  "contraction",
] as const;

export const CONTEXT_DIMENSIONS = [
  "location",
  "activity",
  "time_of_day",
  "caregiver_present",
  "assistive_device",
  "social_setting",
] as const;

export const STATE_TEMPORAL_STATUSES = [
  "current",
  "recent",
  "historical",
  "baseline",
  "transitional",
] as const;

export const RECONSTRUCTION_CONFIDENCE = [
  "well_supported",
  "moderately_supported",
  "weakly_supported",
  "insufficient_evidence",
  "contradicted",
] as const;

export const FAILURE_TAXONOMY = [
  "STATE_RECONSTRUCTION_FAILURE",
  "SNAPSHOT_STATE_ERROR",
  "LATEST_NOTE_AS_STATE",
  "HISTORICAL_STATE_AS_CURRENT",
  "CURRENT_STATE_AS_HISTORY",
  "STATE_SUPERSESSION_FAILURE",
  "STATE_CONTRADICTION_COLLAPSE",
  "STATE_UNCERTAINTY_LOSS",
  "STATE_CONTEXT_LOSS",
  "STATE_DOMAIN_COLLAPSE",
  "GLOBAL_STATE_OVERGENERALIZATION",
  "FUNCTIONAL_STATE_ERROR",
  "COGNITIVE_STATE_ERROR",
  "MEDICATION_STATE_ERROR",
  "CARE_NETWORK_STATE_ERROR",
  "OPERATIONAL_STATE_ERROR",
  "BASELINE_STATE_LOSS",
  "INTERMEDIATE_STATE_LOSS",
  "IMPROVEMENT_STATE_LOSS",
  "STATE_FROM_DIAGNOSIS",
  "STATE_FROM_SINGLE_EVENT",
  "STATE_FROM_SINGLE_CLAIM",
  "STALE_STATE_RECONSTRUCTION",
  "UNSUPPORTED_CURRENT_STATE",
  "STATE_PROVENANCE_LOSS",
  "STATE_TEMPORAL_ERROR",
  "STATE_DEPENDENCY_FAILURE",
  "MIXED_CONTEXT_STATE_COLLAPSE",
] as const;

export const BENCHMARK_REQUIREMENTS = [
  "reconstruct_current_state_from_distributed_evidence",
  "distinguish_current_from_historical_state",
  "incorporate_supersession",
  "preserve_unresolved_contradictions",
  "propagate_uncertainty",
  "preserve_context_specific_states",
  "reconstruct_multiple_domains_independently",
  "distinguish_capability_from_diagnosis",
  "reconstruct_medication_state_separately_from_prescription_state",
  "reconstruct_caregiver_responsibility",
  "avoid_snapshot_only_reasoning",
  "preserve_improvements",
  "identify_stable_domains",
  "avoid_inventing_state_where_evidence_insufficient",
  "produce_traceable_state_to_underlying_claims",
] as const;

export type CareStateDomain = (typeof CARE_STATE_DOMAINS)[number];
export type PhysicalSubdomain = (typeof PHYSICAL_SUBDOMAINS)[number];
export type CognitiveSubdomain = (typeof COGNITIVE_SUBDOMAINS)[number];
export type FunctionalSubdomain = (typeof FUNCTIONAL_SUBDOMAINS)[number];
export type MedicationSubdomain = (typeof MEDICATION_SUBDOMAINS)[number];
export type CareNetworkSubdomain = (typeof CARE_NETWORK_SUBDOMAINS)[number];
export type OperationalSubdomain = (typeof OPERATIONAL_SUBDOMAINS)[number];
export type StateReconstructionStage = (typeof STATE_RECONSTRUCTION_STAGES)[number];
export type ClaimStatus = (typeof CLAIM_STATUSES)[number];
export type EvidenceWeight = (typeof EVIDENCE_WEIGHTS)[number];
export type UncertaintyLevel = (typeof UNCERTAINTY_LEVELS)[number];
export type ContradictionType = (typeof CONTRADICTION_TYPES)[number];
export type SupersessionRelation = (typeof SUPERSESSION_RELATIONS)[number];
export type ContextDimension = (typeof CONTEXT_DIMENSIONS)[number];
export type StateTemporalStatus = (typeof STATE_TEMPORAL_STATUSES)[number];
export type ReconstructionConfidence = (typeof RECONSTRUCTION_CONFIDENCE)[number];
export type FailureTaxonomy = (typeof FAILURE_TAXONOMY)[number];
export type BenchmarkRequirement = (typeof BENCHMARK_REQUIREMENTS)[number];