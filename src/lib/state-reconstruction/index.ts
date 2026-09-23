/**
 * State Reconstruction — Barrel Export
 * SoT: docs/02-product/solenos-state-reconstruction.md
 */

export {
  STATE_RECONSTRUCTION_IDENTITY,
  STATE_RECONSTRUCTION_PURPOSE,
  STATE_RECONSTRUCTION_NOT,
  CARE_STATE_DOMAINS,
  PHYSICAL_SUBDOMAINS,
  COGNITIVE_SUBDOMAINS,
  FUNCTIONAL_SUBDOMAINS,
  MEDICATION_SUBDOMAINS,
  CARE_NETWORK_SUBDOMAINS,
  OPERATIONAL_SUBDOMAINS,
  STATE_RECONSTRUCTION_STAGES,
  CLAIM_STATUSES,
  EVIDENCE_WEIGHTS,
  UNCERTAINTY_LEVELS,
  CONTRADICTION_TYPES,
  SUPERSESSION_RELATIONS,
  CONTEXT_DIMENSIONS,
  STATE_TEMPORAL_STATUSES,
  RECONSTRUCTION_CONFIDENCE,
  FAILURE_TAXONOMY,
  BENCHMARK_REQUIREMENTS,
  type CareStateDomain,
  type PhysicalSubdomain,
  type CognitiveSubdomain,
  type FunctionalSubdomain,
  type MedicationSubdomain,
  type CareNetworkSubdomain,
  type OperationalSubdomain,
  type StateReconstructionStage,
  type ClaimStatus,
  type EvidenceWeight,
  type UncertaintyLevel,
  type ContradictionType,
  type SupersessionRelation,
  type ContextDimension,
  type StateTemporalStatus,
  type ReconstructionConfidence,
  type FailureTaxonomy,
  type BenchmarkRequirement,
} from "./contract-constants";

export type {
  Claim,
  ContextualState,
  DomainState,
  StateTrajectoryPoint,
  Contradiction,
  EvidenceSummary,
  CareState,
  OpenLoop,
  TraceabilityMap,
  EvidenceNode,
  ResolvedEntity,
  EvidenceGraph,
  SupersessionChain,
  SupersessionLink,
  ContradictionSet,
  ReconstructionInput,
  ReconstructionResult,
  ReconstructionMetadata,
  StateChange,
  StateChangeReport,
  DomainDefinition,
} from "./types";

export {
  buildEvidenceGraph,
  extractClaimsFromEvent,
  createDefaultDomainDefinitions,
  resolveEntities,
} from "./evidence-graph";

export {
  detectSupersession,
  applySupersession,
  mergeSupersessionChains,
  getCurrentClaim,
  getClaimTrajectory,
  validateSupersessionChain,
  buildSupersessionChain,
  determineSupersessionRelation,
} from "./supersession";

export {
  detectContradictions,
  attemptContradictionResolution,
  formatContradictionForOutput,
  findContradictionsInGroup,
  checkContradiction,
} from "./contradiction";

export {
  propagateUncertainty,
  propagateContextualUncertainty,
  assessStability,
  formatUncertaintyForOutput,
  buildUncertaintyNarrative,
  calculateReconstructionConfidence,
} from "./uncertainty";

export {
  extractContextualStates,
  mergeContextualStates,
  hasContextVariance,
  formatContextualState,
  getContextValuesForDimension,
  findContextualStates,
  buildContextualState,
} from "./context";

export {
  DomainReconstructor,
  PhysicalDomainReconstructor,
  CognitiveDomainReconstructor,
  FunctionalDomainReconstructor,
  MedicationDomainReconstructor,
  CareNetworkDomainReconstructor,
  OperationalDomainReconstructor,
  getDomainReconstructor,
  reconstructAllDomains,
} from "./domain-reconstruction";

export {
  StateReconstructionEngine,
  stateReconstructionEngine,
  reconstructCareState,
} from "./engine";

export {
  processStateReconstruction,
  quickReconstructState,
  getStateReconstructionEngine,
  formatCareStateForCaregiver,
  formatStateChangeReport,
  extractStateInsights,
  type ProcessStateReconstructionInput,
  type StateReconstructionLayerPayload,
} from "./pipeline";

export {
  runBenchmarkSuite,
  printBenchmarkSummary,
  runFullBenchmark,
  type BenchmarkCase,
  type BenchmarkResult,
} from "./benchmark";

export {
  validateCareState,
  validateReconstructionResult,
  formatValidationReport,
} from "./benchmark/validators";