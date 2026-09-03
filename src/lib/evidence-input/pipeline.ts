/**
 * Evidence & Input Intelligence — Main Processing Pipeline
 *
 * Orchestrates the full flow: Input → Representation → Evidence → Normalized View
 * with all safety mechanisms active.
 */

import type {
  Attribution,
  ConfidenceDimensions,
  EvidenceObject,
  EvidenceQuality,
  EvidenceType,
  ImmutableInput,
  IngestionResult,
  InputStatus,
  NegationStatus,
  NormalizedEvidence,
  ParsedDocument,
  ProcessingFailure,
  ProvenanceChain,
  SourceLocation,
  Temporality,
} from "./types";
import { storeImmutableInput, getImmutableInput } from "./input-storage";
import { createEvidenceObject, validateEvidenceObject } from "./evidence-object";
import { createProvenanceChain, verifyProvenanceChain } from "./provenance";
import { detectNegation, detectAttribution } from "./negation-attribution";
import { computeConfidence, assessEvidenceQuality } from "./confidence-quality";
import { normalizeEvidence, verifyNormalizationPreservation } from "./normalization";
import { detectDuplicates } from "./duplicate-detection";
import { validateAgainstFailureModes, createProcessingFailure } from "./failure-defenses";

// ─── INPUT INGESTION ─────────────────────────────────────────────────────────

/**
 * Ingest a raw input into the system.
 * The original bytes are stored immutably.
 */
export function ingestInput(params: {
  original_bytes: Buffer | Uint8Array;
  content_type?: string | null;
  original_filename?: string | null;
  received_from?: string | null;
  ingestion_metadata?: Record<string, unknown>;
}): ImmutableInput {
  return storeImmutableInput(params);
}

// ─── DOCUMENT PARSING ────────────────────────────────────────────────────────

/**
 * Parse an input into a structured document representation.
 * Preserves layout, reading order, and section context.
 *
 * In a production system, this would use OCR/layout analysis.
 * Here we provide the structure that wraps any parser output.
 */
export function parseDocument(params: {
  input_id: string;
  page_count: number;
  regions: ParsedDocument["regions"];
  section_tree?: ParsedDocument["section_tree"];
  parse_confidence: number;
  parser_version?: string;
}): ParsedDocument | null {
  const input = getImmutableInput(params.input_id);
  if (!input) return null;

  return {
    document_id: `doc_${params.input_id}`,
    input_id: params.input_id,
    page_count: params.page_count,
    regions: params.regions,
    section_tree: params.section_tree ?? [],
    reading_order: params.regions.sort((a, b) => a.reading_order - b.reading_order).map((r) => r.region_id),
    parse_timestamp: new Date().toISOString(),
    parse_model_version: params.parser_version ?? "parser-v1",
    parse_confidence: params.parse_confidence,
  };
}

// ─── EVIDENCE EXTRACTION ─────────────────────────────────────────────────────

/**
 * Extract evidence from a parsed document region.
 * This is the core extraction function — it creates a single evidence object
 * with full provenance, attribution, negation detection, and confidence.
 */
export function extractEvidence(params: {
  input: ImmutableInput;
  parsed_document: ParsedDocument;
  source_location: SourceLocation;
  content_type: EvidenceType;
  content_value: Record<string, unknown>;
  original_text: string;
  temporal_status?: Temporality;
  event_date?: string | null;
  date_confidence?: number | null;
  negation_override?: NegationStatus;
  attribution_override?: Partial<Attribution>;
  subject?: string;
  ocr_confidence?: number | null;
  extraction_confidence?: number;
  entity_normalization_confidence?: number | null;
  temporal_extraction_confidence?: number | null;
  source_type?: string;
  is_complete?: boolean;
  legibility?: "clear" | "degraded" | "illegible";
  specificity?: "specific" | "vague";
}): EvidenceObject | null {
  const {
    input,
    parsed_document,
    source_location,
    content_type,
    content_value,
    original_text,
    subject = "patient",
  } = params;

  // Detect negation
  const negation = detectNegation(original_text);
  const negation_status = params.negation_override ?? negation.status;
  const certainty_level = negation.confidence;

  // Detect attribution
  const attribution_base = detectAttribution(original_text, subject);
  const attribution: Attribution = {
    ...attribution_base,
    ...params.attribution_override,
  };

  // Create provenance chain
  const provenance = createProvenanceChain({
    input_id: input.input_id,
    ingestion_timestamp: input.received_at,
    source_location,
    parse_model_version: parsed_document.parse_model_version,
    extraction_model_version: "evidence-input-v1.0.0",
  });

  // Verify provenance
  const provenance_check = verifyProvenanceChain(provenance);
  if (!provenance_check.valid) {
    // Still create evidence but note the issue
    console.warn("Provenance verification issues:", provenance_check.issues);
  }

  // Compute confidence
  const confidence: ConfidenceDimensions = computeConfidence({
    ocr_confidence: params.ocr_confidence ?? null,
    parse_confidence: parsed_document.parse_confidence,
    extraction_confidence: params.extraction_confidence ?? 0.7,
    entity_normalization_confidence: params.entity_normalization_confidence ?? null,
    temporal_extraction_confidence: params.temporal_extraction_confidence ?? null,
    negation_detection_confidence: negation_status === "affirmed" ? 0.8 : 0.9,
  });

  // Assess quality
  const quality: EvidenceQuality = assessEvidenceQuality({
    source_type: params.source_type ?? "general_document",
    is_complete: params.is_complete ?? true,
    legibility: params.legibility ?? "clear",
    reporting_type: attribution.reporting_type,
    specificity: params.specificity ?? "specific",
  });

  // Create the evidence object
  const evidence = createEvidenceObject({
    provenance,
    attribution,
    content_type,
    content_value,
    original_text,
    temporal_status: params.temporal_status ?? "present",
    event_date: params.event_date ?? null,
    date_confidence: params.date_confidence ?? null,
    negation_status,
    certainty_level,
    confidence,
    quality,
  });

  // Validate the evidence object
  const validation = validateEvidenceObject(evidence);
  if (!validation.valid) {
    console.warn("Evidence object validation failed:", validation.missing_fields);
    return null;
  }

  return evidence;
}

// ─── FULL PIPELINE ───────────────────────────────────────────────────────────

/**
 * Run the full Evidence & Input Intelligence pipeline.
 * Takes an input through all stages and returns the complete result.
 */
export function runEvidenceInputPipeline(params: {
  original_bytes: Buffer | Uint8Array;
  content_type?: string | null;
  original_filename?: string | null;
  received_from?: string | null;
  parsed_document?: ParsedDocument;
  extractions: Array<{
    source_location: SourceLocation;
    content_type: EvidenceType;
    content_value: Record<string, unknown>;
    original_text: string;
    temporal_status?: Temporality;
    event_date?: string | null;
    date_confidence?: number | null;
    subject?: string;
    ocr_confidence?: number | null;
    extraction_confidence?: number;
    entity_normalization_confidence?: number | null;
    temporal_extraction_confidence?: number | null;
  }>;
}): IngestionResult {
  const failures: ProcessingFailure[] = [];

  // Stage 1: Ingest input
  const input = storeImmutableInput({
    original_bytes: params.original_bytes,
    content_type: params.content_type,
    original_filename: params.original_filename,
    received_from: params.received_from,
  });

  // Stage 2: Extract evidence
  const evidence_objects: EvidenceObject[] = [];
  const parsed_doc = params.parsed_document ?? null;

  if (!parsed_doc) {
    failures.push(
      createProcessingFailure({
        failure_mode: "table_corruption",
        description: "No parsed document provided — extraction may be limited",
        severity: "warning",
      }),
    );
  }

  for (const ext of params.extractions) {
    if (!parsed_doc) continue;

    const evidence = extractEvidence({
      input,
    parsed_document: parsed_doc ?? null,
      source_location: ext.source_location,
      content_type: ext.content_type,
      content_value: ext.content_value,
      original_text: ext.original_text,
      temporal_status: ext.temporal_status,
      event_date: ext.event_date,
      date_confidence: ext.date_confidence,
      subject: ext.subject,
      ocr_confidence: ext.ocr_confidence,
      extraction_confidence: ext.extraction_confidence,
      entity_normalization_confidence: ext.entity_normalization_confidence,
      temporal_extraction_confidence: ext.temporal_extraction_confidence,
    });

    if (evidence) {
      // Run failure mode validations
      const evidence_failures = validateAgainstFailureModes(evidence);
      failures.push(...evidence_failures);
      evidence_objects.push(evidence);
    }
  }

  // Stage 3: Normalize evidence
  const normalized_evidence: NormalizedEvidence[] = [];
  for (const evidence of evidence_objects) {
    const normalized = normalizeEvidence(evidence);
    const preservation_check = verifyNormalizationPreservation(evidence, normalized);
    if (!preservation_check.preserved) {
      failures.push(
        createProcessingFailure({
          failure_mode: "normalization_destroying_source",
          description: `Normalization issue: ${preservation_check.issues.join(", ")}`,
          severity: "warning",
          affected_evidence_ids: [evidence.evidence_id],
        }),
      );
    }
    normalized_evidence.push(normalized);
  }

  // Stage 4: Detect duplicates
  const duplicates = detectDuplicates(evidence_objects);

  return {
    input,
    status: failures.some((f) => f.severity === "critical") ? "failed_extraction" : "extracted",
    parsed_document: parsed_doc,
    evidence_objects,
    normalized_evidence,
    duplicates,
    failures,
    processing_timestamp: new Date().toISOString(),
  };
}
