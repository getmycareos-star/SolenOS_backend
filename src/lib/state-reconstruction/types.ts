/**
 * State Reconstruction — Core Types
 * SoT: docs/02-product/solenos-state-reconstruction.md
 */

import type {
  CareStateDomain,
  PhysicalSubdomain,
  CognitiveSubdomain,
  FunctionalSubdomain,
  MedicationSubdomain,
  CareNetworkSubdomain,
  OperationalSubdomain,
  ClaimStatus,
  EvidenceWeight,
  UncertaintyLevel,
  ContradictionType,
  SupersessionRelation,
  ContextDimension,
  StateTemporalStatus,
  ReconstructionConfidence,
  FailureTaxonomy,
  BenchmarkRequirement,
  StateReconstructionStage,
} from "./contract-constants";

// Re-export the contract-constant types so consumers can import them from
// "./types" (the canonical entry point for this module's type surface).
export type {
  CareStateDomain,
  PhysicalSubdomain,
  CognitiveSubdomain,
  FunctionalSubdomain,
  MedicationSubdomain,
  CareNetworkSubdomain,
  OperationalSubdomain,
  ClaimStatus,
  EvidenceWeight,
  UncertaintyLevel,
  ContradictionType,
  SupersessionRelation,
  ContextDimension,
  StateTemporalStatus,
  ReconstructionConfidence,
  FailureTaxonomy,
  BenchmarkRequirement,
  StateReconstructionStage,
};

/** Represents a single claim extracted from evidence */
export type Claim = {
  id: string;
  domain: CareStateDomain;
  subdomain: string;
  statement: string;
  context: Record<ContextDimension, string | null>;
  temporal_status: StateTemporalStatus;
  valid_from: string;
  valid_until: string | null;
  superseded_by: string | null;
  supersession_relation: SupersessionRelation | null;
  source_event_ids: string[];
  evidence_weight: EvidenceWeight;
  uncertainty: UncertaintyLevel;
  uncertainty_reason: string | null;
  confidence: ReconstructionConfidence;
  provenance_chain: string[];
  status: ClaimStatus;
};

/** Context-specific state value */
export type ContextualState = {
  context: Record<ContextDimension, string | null>;
  value: string;
  uncertainty: UncertaintyLevel;
  supporting_claim_ids: string[];
  contradicting_claim_ids: string[];
};

/** Domain-specific reconstructed state */
export type DomainState = {
  domain: CareStateDomain;
  subdomain: string;
  current_value: string | null;
  contextual_values: ContextualState[];
  trajectory: StateTrajectoryPoint[];
  uncertainty: UncertaintyLevel;
  uncertainty_narrative: string;
  contradictions: Contradiction[];
  confidence: ReconstructionConfidence;
  evidence_summary: EvidenceSummary;
  stable: boolean;
  last_updated: string;
};

/** Trajectory point showing historical progression */
export type StateTrajectoryPoint = {
  timestamp: string;
  value: string;
  claim_ids: string[];
  temporal_status: StateTemporalStatus;
  is_superseded: boolean;
  supersession_note: string | null;
};

/** Contradiction between sources */
export type Contradiction = {
  id: string;
  type: ContradictionType;
  claim_a_id: string;
  claim_b_id: string;
  claim_a_summary: string;
  claim_b_summary: string;
  context_a: Record<ContextDimension, string | null>;
  context_b: Record<ContextDimension, string | null>;
  resolution_attempted: boolean;
  resolution_note: string | null;
  preserved: boolean;
};

/** Evidence supporting a domain state */
export type EvidenceSummary = {
  total_claims: number;
  active_claims: number;
  superseded_claims: number;
  contradicted_claims: number;
  uncertain_claims: number;
  source_diversity: EvidenceWeight[];
  earliest_evidence: string | null;
  latest_evidence: string | null;
  gap_periods: string[];
};

/** Full Care State reconstruction */
export type CareState = {
  care_recipient_id: string;
  reconstructed_at: string;
  as_of: string;
  domains: DomainState[];
  open_loops: OpenLoop[];
  overall_confidence: ReconstructionConfidence;
  traceability: TraceabilityMap[];
  failures: FailureTaxonomy[];
};

/** Open loop / unresolved question */
export type OpenLoop = {
  id: string;
  domain: CareStateDomain;
  subdomain: string;
  question: string;
  why_it_matters: string;
  related_claim_ids: string[];
  blocking: string[];
  first_noted: string;
  last_updated: string;
};

/** Traceability from state to claims to evidence */
export type TraceabilityMap = {
  state_element: string;
  claim_ids: string[];
  event_ids: string[];
  provenance_records: string[];
};

/** Evidence graph node */
export type EvidenceNode = {
  id: string;
  event_id: string;
  event_type: string;
  timestamp: string;
  ingestion_time: string;
  claims: Claim[];
  entities: ResolvedEntity[];
  supersedes: string[];
  superseded_by: string[];
  contradictions: string[];
};

/** Resolved entity in evidence graph */
export type ResolvedEntity = {
  id: string;
  canonical_name: string;
  aliases: string[];
  type: "person" | "place" | "institution" | "object" | "medication" | "condition";
  confidence: number;
  source_event_ids: string[];
};

/** Evidence graph for reconstruction */
export type EvidenceGraph = {
  care_recipient_id: string;
  nodes: EvidenceNode[];
  entities: ResolvedEntity[];
  claim_index: Map<string, Claim>;
  supersession_chains: SupersessionChain[];
  contradiction_sets: ContradictionSet[];
};

/** Supersession chain for a claim */
export type SupersessionChain = {
  original_claim_id: string;
  chain: SupersessionLink[];
  current_claim_id: string | null;
};

/** Link in supersession chain */
export type SupersessionLink = {
  from_claim_id: string;
  to_claim_id: string;
  relation: SupersessionRelation;
  timestamp: string;
  reason: string;
};

/** Set of related contradictions */
export type ContradictionSet = {
  id: string;
  claims: string[];
  type: ContradictionType;
  context_variance: Record<ContextDimension, string[]>;
  preserved: boolean;
};

/** Reconstruction input */
export type ReconstructionInput = {
  care_recipient_id: string;
  events: import("../situation-entry/types").CanonicalCareEvent[];
  as_of?: string;
  baseline?: import("../baseline-intelligence-engine/types").BaselineIntelligenceResult;
  care_reality_profile?: import("../care-reality-profile-engine/types").CareRealityProfileResult;
  continuity_properties?: import("../continuity-properties/types").ContinuityPropertiesResult;
};

/** Reconstruction result */
export type ReconstructionResult = {
  care_state: CareState;
  evidence_graph: EvidenceGraph;
  stages_completed: StateReconstructionStage[];
  reconstruction_metadata: ReconstructionMetadata;
};

/** Metadata about the reconstruction process */
export type ReconstructionMetadata = {
  total_events_processed: number;
  total_claims_extracted: number;
  claims_after_supersession: number;
  contradictions_preserved: number;
  uncertainty_propagated: number;
  contexts_preserved: number;
  domains_reconstructed: number;
  traceability_completeness: number;
  duration_ms: number;
};

/** State change detection */
export type StateChange = {
  id: string;
  domain: CareStateDomain;
  subdomain: string;
  prior_state: DomainState | null;
  current_state: DomainState;
  change_kind: "improvement" | "decline" | "new" | "resolved" | "fluctuation" | "uncertainty_change";
  magnitude: "significant" | "moderate" | "minimal";
  evidence_ids: string[];
  detected_at: string;
  confidence: ReconstructionConfidence;
};

/** State change report */
export type StateChangeReport = {
  care_recipient_id: string;
  as_of: string;
  changes: StateChange[];
  stable_domains: CareStateDomain[];
  new_open_loops: OpenLoop[];
  resolved_open_loops: OpenLoop[];
  overall_trajectory: "improving" | "declining" | "stable" | "mixed" | "uncertain";
};