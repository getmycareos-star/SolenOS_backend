/**
 * Evidence & Input Intelligence — Unified Module
 *
 * The foundational capability responsible for:
 * 1. Accepting arbitrary real-world inputs into SolenOS
 * 2. Preserving those inputs immutably in their original form
 * 3. Parsing inputs into structured representations
 * 4. Extracting evidence objects with full provenance
 * 5. Preserving uncertainty at every level
 * 6. Producing normalized representations without destroying originals
 *
 * Architecture: Input → Representation → Evidence → Normalized View
 *
 * Invariants (NEVER violated):
 * - Provenance always preserved — every evidence object traces to source
 * - Originals always preserved — normalization is additive, never destructive
 * - Uncertainty always preserved — confidence, negation, qualification survive
 *
 * Boundary: This primitive extracts and preserves evidence.
 * It does NOT interpret evidence clinically, infer unstated facts,
 * assess truth of source statements, or predict future states.
 */

// ─── CORE TYPES ───────────────────────────────────────────────────────────────

export type {
  // Input layer
  ImmutableInput,
  InputType,
  InputStatus,
  // Representation layer
  PageRegion,
  ParsedDocument,
  SectionNode,
  // Evidence layer
  EvidenceType,
  NegationStatus,
  Temporality,
  ReportingType,
  Attribution,
  SourceLocation,
  ProvenanceChain,
  TransformationStep,
  // Confidence layer
  ConfidenceDimensions,
  // Quality layer
  EvidenceQuality,
  // The irreducible evidence object
  EvidenceObject,
  // Normalized evidence
  NormalizedEvidence,
  CanonicalCode,
  // Duplicate detection
  DuplicateType,
  DuplicateEvidence,
  // Failure modes
  FailureMode,
  FailureDefense,
  // Processing results
  IngestionResult,
  ProcessingFailure,
} from "./types";

// ─── INPUT STORAGE ───────────────────────────────────────────────────────────

export {
  storeImmutableInput,
  getImmutableInput,
  verifyInputIntegrity,
  getAllInputs,
  findDuplicateInput,
  clearInputStore,
} from "./input-storage";

// ─── NEGATION & ATTRIBUTION ──────────────────────────────────────────────────

export {
  detectNegation,
  detectAttribution,
  createSourceLocation,
} from "./negation-attribution";

// ─── CONFIDENCE & QUALITY ────────────────────────────────────────────────────

export {
  computeConfidence,
  assessEvidenceQuality,
  verifyConfidenceQualitySeparation,
} from "./confidence-quality";

// ─── EVIDENCE OBJECT ─────────────────────────────────────────────────────────

export {
  createEvidenceObject,
  supersedeEvidenceObject,
  validateEvidenceObject,
  getExtractionModelVersion,
} from "./evidence-object";

// ─── PROVENANCE ──────────────────────────────────────────────────────────────

export {
  createProvenanceChain,
  verifyProvenanceChain,
  formatProvenanceChain,
} from "./provenance";

// ─── DUPLICATE DETECTION ─────────────────────────────────────────────────────

export {
  computeEvidenceFingerprint,
  detectDuplicates,
  areDuplicates,
  getAllDuplicates,
  clearDuplicateStore,
} from "./duplicate-detection";

// ─── NORMALIZATION ───────────────────────────────────────────────────────────

export {
  normalizeMedication,
  normalizeCondition,
  normalizeDate,
  normalizeEvidence,
  verifyNormalizationPreservation,
} from "./normalization";

// ─── FAILURE DEFENSES ────────────────────────────────────────────────────────

export {
  initializeFailureDefenses,
  getFailureDefenses,
  triggerFailureDefense,
  createProcessingFailure,
  validateAgainstFailureModes,
} from "./failure-defenses";

// ─── PIPELINE ────────────────────────────────────────────────────────────────

export {
  ingestInput,
  parseDocument,
  extractEvidence,
  runEvidenceInputPipeline,
} from "./pipeline";
