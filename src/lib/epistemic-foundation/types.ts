import type {
  EvidenceKind,
  EpistemicStatus,
  ClaimLifecycleStatus,
  InferenceConfidenceDimension,
  UncertaintyLevel,
  AbsenceModel,
  ReasoningType,
  EpistemicRule,
  EpistemicViolation,
  FormalRule,
} from "./contract-constants";

export type {
  EvidenceKind,
  EpistemicStatus,
  ClaimLifecycleStatus,
  InferenceConfidenceDimension,
  UncertaintyLevel,
  AbsenceModel,
  ReasoningType,
  EpistemicRule,
  EpistemicViolation,
  FormalRule,
} from "./contract-constants";

export type EvidenceId = string;
export type ClaimId = string;
export type InferenceId = string;
export type SourceId = string;
export type DerivationId = string;

export interface EvidenceSource {
  id: SourceId;
  type: EvidenceKind;
  label: string;
  capturedAt: string;
  rawInputId?: string;
  eventId?: string;
  documentId?: string;
  contributorId?: string;
}

export interface EvidenceItem {
  id: EvidenceId;
  source: EvidenceSource;
  content: string;
  extractedFact?: string;
  confidence?: number;
  reliability?: number;
  temporalScope?: {
    start?: string;
    end?: string;
    isHistorical: boolean;
    isCurrent: boolean;
  };
  contradictions: EvidenceId[];
  qualityFlags: string[];
  createdAt: string;
}

export interface ClaimProvenance {
  evidenceIds: EvidenceId[];
  derivationIds: DerivationId[];
  inferenceIds: InferenceId[];
  sourceClaims: ClaimId[];
  originalSources: EvidenceSource[];
  reasoningSummary?: string;
  extractionVersion?: string;
  modelVersion?: string;
  promptVersion?: string;
}

export interface Claim {
  id: ClaimId;
  statement: string;
  status: EpistemicStatus;
  lifecycleStatus: ClaimLifecycleStatus;
  subject: string;
  temporalScope?: {
    start?: string;
    end?: string;
    isPointInTime: boolean;
  };
  confidence: number;
  uncertaintyLevel: UncertaintyLevel;
  provenance: ClaimProvenance;
  assumptions: string[];
  contradictions: ClaimId[];
  supersededBy?: ClaimId;
  supersedes?: ClaimId;
  createdAt: string;
  updatedAt: string;
  invalidatedAt?: string;
  stalenessIndicator?: {
    lastVerified: string;
    isStale: boolean;
    stalenessReason?: string;
  };
}

export interface InferenceInput {
  evidenceIds: EvidenceId[];
  claimIds: ClaimId[];
  assumptions: string[];
}

export interface InferenceConfidence {
  extractionCorrectness: number;
  evidenceReliability: number;
  inferenceValidity: number;
  overall: number;
  dimensions: Record<InferenceConfidenceDimension, number>;
}

export interface Inference {
  id: InferenceId;
  statement: string;
  inputs: InferenceInput;
  reasoningType: ReasoningType;
  reasoningSteps: string[];
  confidence: InferenceConfidence;
  assumptions: string[];
  contradictoryEvidence: EvidenceId[];
  contradictoryClaims: ClaimId[];
  status: "proposed" | "active" | "validated" | "rejected" | "superseded";
  createdAt: string;
  updatedAt: string;
  validatedAt?: string;
  validationEvidence?: EvidenceId[];
}

export interface DerivationStep {
  id: DerivationId;
  operation: "combine" | "aggregate" | "temporal_reasoning" | "pattern_match" | "causal_inference";
  inputIds: (EvidenceId | ClaimId | DerivationId)[];
  outputClaimId: ClaimId;
  reasoning: string;
  timestamp: string;
}

export interface ContradictionRecord {
  id: string;
  claimA: ClaimId;
  claimB: ClaimId;
  evidenceA: EvidenceId[];
  evidenceB: EvidenceId[];
  resolution: "unresolved" | "preferred_a" | "preferred_b" | "both_valid" | "superseded";
  preferredClaimId?: ClaimId;
  resolutionReason?: string;
  resolvedAt?: string;
  createdAt: string;
}

export interface KnowledgeRevisionEntry {
  id: string;
  claimId: ClaimId;
  previousStatus?: EpistemicStatus;
  previousStatement?: string;
  newStatus: EpistemicStatus;
  newStatement?: string;
  triggerEvidence: EvidenceId[];
  reason: string;
  timestamp: string;
}

export interface UnknownState {
  subject: string;
  predicate: string;
  absenceModel: AbsenceModel;
  lastChecked: string;
  confidence: number;
  notes?: string;
}

export interface AssumptionRecord {
  id: string;
  statement: string;
  attachedToInferenceId?: InferenceId;
  attachedToClaimId?: ClaimId;
  risk: "low" | "medium" | "high";
  explicit: boolean;
  createdAt: string;
}

export interface EpistemicViolationRecord {
  id: string;
  violation: EpistemicViolation;
  rule: string;
  description: string;
  objectId?: string;
  objectType?: "evidence" | "claim" | "inference";
  timestamp: string;
  corrected: boolean;
  correction?: string;
}

export interface EpistemicFoundationQuery {
  claimId?: ClaimId;
  evidenceId?: EvidenceId;
  inferenceId?: InferenceId;
  subject?: string;
  status?: EpistemicStatus;
  includeSuperseded?: boolean;
  includeContradictions?: boolean;
  asOf?: string;
}

export interface ClaimTraceResult {
  claim: Claim;
  provenance: ClaimProvenance;
  supportingEvidence: EvidenceItem[];
  supportingClaims: Claim[];
  sourceClaims: Claim[];
  derivationChain: DerivationStep[];
  inferences: Inference[];
  contradictions: ContradictionRecord[];
  revisionHistory: KnowledgeRevisionEntry[];
}

export interface EpistemicFoundationResult {
  known: Claim[];
  inferred: Inference[];
  unknown: UnknownState[];
  unsupported: Claim[];
  contradicted: Claim[];
  evidence: EvidenceItem[];
  violations: EpistemicViolationRecord[];
  totalClaims: number;
  totalEvidence: number;
  totalInferences: number;
}

export interface EvidenceToClaimTraceResult {
  evidence: EvidenceItem;
  directClaims: Claim[];
  derivedClaims: Claim[];
  inferredClaims: Claim[];
  contradictions: ContradictionRecord[];
}

export interface InferenceValidationResult {
  inference: Inference;
  isValid: boolean;
  violations: EpistemicViolationRecord[];
  missingSupport: {
    evidence: EvidenceId[];
    claims: ClaimId[];
  };
  contradictoryEvidence: EvidenceItem[];
  contradictoryClaims: Claim[];
}
