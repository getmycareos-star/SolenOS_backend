import type {
  COMPATIBILITY_STATUS,
  CONFLICT_RESOLUTION_STATUS,
  CONFLICT_TYPES,
  EVIDENCE_DERIVATION,
  RESOLUTION_MECHANISMS,
  SOURCE_LINEAGE_RELATIONSHIP,
} from "./contract-constants";

export type EvidenceStatusValue = (typeof EVIDENCE_DERIVATION)[number];
export type SourceLineageValue = (typeof SOURCE_LINEAGE_RELATIONSHIP)[number];
export type ConflictTypeValue = (typeof CONFLICT_TYPES)[number];
export type CompatibilityStatusValue = (typeof COMPATIBILITY_STATUS)[number];
export type ConflictResolutionStatusValue = (typeof CONFLICT_RESOLUTION_STATUS)[number];
export type ResolutionMechanismValue = (typeof RESOLUTION_MECHANISMS)[number];

export type ConflictClaim = {
  claim_id: string;
  subject: string;
  predicate: string;
  object: string;
  raw_text: string;
  temporal_assertion: TemporalAssertion | null;
  evidence_derivation: EvidenceStatusValue;
  source: ClaimSource;
  numeric_confidence: number;
  evidence_status: string;
  created_at: string;
  superseded_by: string | null;
};

export type TemporalAssertion = {
  kind: "event_time" | "document_time" | "approximate" | "inferred" | "unknown";
  value: string | null;
  is_range: boolean;
  range_start: string | null;
  range_end: string | null;
  confidence: number;
};

export type ClaimSource = {
  source_id: string;
  source_type: "caregiver" | "document" | "medical_record" | "voice_transcript" | "inferred" | "unknown";
  source_label: string;
  caregiver_id: string | null;
  document_id: string | null;
  raw_input_id: string | null;
  lineage: SourceLineage | null;
};

export type SourceLineage = {
  relationship: SourceLineageValue;
  derived_from_source_id: string | null;
  derivation_description: string | null;
};

export type ConflictObject = {
  conflict_id: string;
  conflict_type: ConflictTypeValue;
  compatibility_status: CompatibilityStatusValue;
  resolution_status: ConflictResolutionStatusValue;
  claims: ConflictClaim[];
  temporal_scope: TemporalScope | null;
  explanation: string;
  resolution_evidence: ResolutionEvidence | null;
  resolution_mechanism: ResolutionMechanismValue | null;
  detected_at: string;
  last_reviewed_at: string;
  history: ConflictHistoryEntry[];
};

export type TemporalScope = {
  kind: "point_in_time" | "range" | "ongoing" | "unknown";
  value: string | null;
  range_start: string | null;
  range_end: string | null;
  confidence: number;
};

export type ResolutionEvidence = {
  mechanism: ResolutionMechanismValue;
  evidence_claim_ids: string[];
  description: string;
  resolved_at: string;
  resolved_by: "system" | "user" | "caregiver";
  previous_status: ConflictResolutionStatusValue;
};

export type ConflictHistoryEntry = {
  timestamp: string;
  action: "detected" | "reviewed" | "provisionally_resolved" | "resolved" | "superseded" | "invalidated" | "reopened";
  from_status: ConflictResolutionStatusValue | null;
  to_status: ConflictResolutionStatusValue;
  reason: string;
  actor: "system" | "user" | "caregiver";
};

export type DetectConflictsInput = {
  new_claim: ConflictClaim;
  existing_claims: ConflictClaim[];
  temporal_context: {
    current_time: string;
    event_timeline: Array<{ time: string; event: string; source_id: string }>;
  };
};

export type DetectConflictsOutput = {
  conflicts: ConflictObject[];
  apparent_conflicts_resolved: Array<{
    conflict_id: string;
    resolution: "temporal_clarification" | "state_transition_identified" | "specificity_reconciled";
    explanation: string;
  }>;
  no_conflict: boolean;
};

export type CompatibilityInput = {
  claim_a: ConflictClaim;
  claim_b: ConflictClaim;
  temporal_context: {
    current_time: string;
    claim_a_temporal: TemporalAssertion | null;
    claim_b_temporal: TemporalAssertion | null;
  };
};

export type CompatibilityOutput = {
  status: CompatibilityStatusValue;
  analysis: string;
  blocking_factors: string[];
  reconciling_factors: string[];
};

export type ConflictResolutionInput = {
  mechanism: ResolutionMechanismValue;
  reason: string;
  evidenceClaimIds: string[];
  actor: "system" | "user" | "caregiver";
};
