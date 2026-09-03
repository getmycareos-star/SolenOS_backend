export {
  CONFLICT_INTELLIGENCE_IDENTITY,
  CONFLICT_INTELLIGENCE_DEFINING_PRINCIPLE,
  NO_SILENT_RESOLUTION_POLICY,
  COMPATIBILITY_STATUS,
  CONFLICT_RESOLUTION_STATUS,
  CONFLICT_TYPES,
  EVIDENCE_DERIVATION,
  SOURCE_LINEAGE_RELATIONSHIP,
  RESOLUTION_MECHANISMS,
  CONTRADICTION_DETECTION_RULES,
} from "./contract-constants";

export type {
  ConflictClaim,
  ClaimSource,
  SourceLineage,
  ConflictObject,
  TemporalScope,
  TemporalAssertion,
  ResolutionEvidence,
  ConflictHistoryEntry,
  DetectConflictsInput,
  DetectConflictsOutput,
  CompatibilityInput,
  CompatibilityOutput,
} from "./types";

export {
  analyzeCompatibility,
} from "./compatibility";

export {
  analyzeTemporalContext,
  createTemporalAssertion,
  createRangeTemporalAssertion,
} from "./temporal-analysis";

export {
  compareSources,
  createSourceLineage,
  createClaimSource,
} from "./source-comparison";

export {
  createConflictObject,
  transitionResolutionStatus,
  reopenConflict,
  supersedeConflict,
  invalidateConflict,
} from "./conflict-object";

export {
  generateConflictExplanation,
  type ConflictExplanation,
} from "./explanation";

export {
  detectConflicts,
  checkConflictReopening,
} from "./detect";

export {
  runConflictIntelligence,
  resolveConflict,
  applyExplicitCorrection,
  invalidateConflictObject,
} from "./pipeline";
