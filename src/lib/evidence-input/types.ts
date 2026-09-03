/**
 * Evidence & Input Intelligence — Core Types
 *
 * The irreducible foundation for turning immutable source inputs into
 * traceable, structured evidence without adding interpretation.
 *
 * Architecture: Input → Representation → Evidence → Normalized View
 * Invariants: Provenance always preserved, originals always preserved,
 *             uncertainty always preserved.
 */

// ─── INPUT LAYER ─────────────────────────────────────────────────────────────

export type InputType =
  | "pdf"
  | "image"
  | "scanned_pdf"
  | "photograph"
  | "screenshot"
  | "handwritten_note"
  | "typed_document"
  | "lab_report"
  | "medication_list"
  | "discharge_summary"
  | "clinical_note"
  | "appointment_document"
  | "prescription"
  | "letter"
  | "form"
  | "care_instruction"
  | "caregiver_note"
  | "insurance_document"
  | "legal_document"
  | "benefits_document"
  | "general_document";

export type InputStatus =
  | "received"
  | "parsing"
  | "parsed"
  | "extracting"
  | "extracted"
  | "failed_parse"
  | "failed_extraction"
  | "unsupported_format"
  | "corrupted";

/**
 * ImmutableInput — the original artifact as received.
 * NEVER modified after creation. Original bytes always preserved.
 */
export type ImmutableInput = {
  readonly input_id: string;
  readonly original_bytes: Buffer | Uint8Array;
  readonly content_hash: string;
  readonly content_type: string | null;
  readonly original_filename: string | null;
  readonly byte_size: number;
  readonly received_at: string;
  readonly received_from: string | null;
  readonly ingestion_metadata: Record<string, unknown>;
};

// ─── REPRESENTATION LAYER ─────────────────────────────────────────────────────

export type PageRegion = {
  region_id: string;
  page_number: number;
  bounding_box: { x: number; y: number; width: number; height: number };
  region_type: "text" | "table" | "image" | "heading" | "footer" | "header" | "form_field" | "handwriting" | "caption" | "footnote" | "list";
  reading_order: number;
  section_path: string[];
  text_content: string | null;
  image_reference: string | null;
  ocr_confidence: number | null;
};

export type ParsedDocument = {
  document_id: string;
  input_id: string;
  page_count: number;
  regions: PageRegion[];
  section_tree: SectionNode[];
  reading_order: string[];
  parse_timestamp: string;
  parse_model_version: string;
  parse_confidence: number;
};

export type SectionNode = {
  section_id: string;
  heading: string | null;
  level: number;
  page_number: number;
  region_ids: string[];
  children: SectionNode[];
};

// ─── EVIDENCE LAYER ───────────────────────────────────────────────────────────

export type EvidenceType =
  | "medication"
  | "condition"
  | "symptom"
  | "care_event"
  | "provider"
  | "appointment"
  | "lab_result"
  | "vital_sign"
  | "instruction"
  | "person"
  | "organization"
  | "date_time"
  | "measurement"
  | "procedure"
  | "allergy"
  | "immunization"
  | "family_history"
  | "social_history"
  | "negated_statement"
  | "other";

export type NegationStatus =
  | "affirmed"
  | "negated"
  | "suspected"
  | "ruled_out"
  | "history_of"
  | "family_history_of"
  | "resolved"
  | "unknown";

export type Temporality =
  | "present"
  | "past"
  | "future"
  | "unknown";

export type ReportingType =
  | "direct_observation"
  | "patient_reported"
  | "caregiver_reported"
  | "family_reported"
  | "documented_in_record"
  | "inferred_by_source"
  | "unknown";

export type Attribution = {
  author: string | null;
  speaker: string | null;
  subject: string;
  reporting_type: ReportingType;
};

export type SourceLocation = {
  input_id: string;
  document_id: string;
  page_number: number | null;
  region_id: string | null;
  text_span: string;
  section_path: string[];
  bounding_box: { x: number; y: number; width: number; height: number } | null;
};

export type ProvenanceChain = {
  original_input_id: string;
  ingestion_timestamp: string;
  parse_timestamp: string;
  extraction_timestamp: string;
  parse_model_version: string;
  extraction_model_version: string;
  source_location: SourceLocation;
  transformation_steps: TransformationStep[];
};

export type TransformationStep = {
  step: string;
  timestamp: string;
  model_version: string;
  input_description: string;
  output_description: string;
};

// ─── CONFIDENCE LAYER ────────────────────────────────────────────────────────

export type ConfidenceDimensions = {
  ocr_confidence: number | null;
  parse_confidence: number;
  extraction_confidence: number;
  entity_normalization_confidence: number | null;
  temporal_extraction_confidence: number | null;
  negation_detection_confidence: number;
  overall_confidence: number;
};

// ─── QUALITY LAYER ───────────────────────────────────────────────────────────

export type EvidenceQuality = {
  quality_score: "high" | "medium" | "low" | "unknown";
  source_type_reliability: "authoritative" | "professional" | "caregiver" | "unknown";
  completeness: "complete" | "partial" | "fragment";
  legibility: "clear" | "degraded" | "illegible";
  directness: "direct" | "reported" | "hearsay";
  timeliness: "current" | "outdated" | "unknown";
  specificity: "specific" | "vague";
  quality_notes: string[];
};

// ─── THE IRREDUCIBLE EVIDENCE OBJECT ─────────────────────────────────────────

/**
 * EvidenceObject — the smallest valid unit of extracted information.
 *
 * Every field is necessary. Remove any and critical information is lost.
 * This is immutable once created. Corrections create new objects.
 */
export type EvidenceObject = {
  evidence_id: string;
  provenance: ProvenanceChain;
  attribution: Attribution;
  content: {
    type: EvidenceType;
    value: Record<string, unknown>;
    original_text: string;
  };
  temporality: {
    temporal_status: Temporality;
    event_date: string | null;
    date_confidence: number | null;
  };
  negation: {
    negation_status: NegationStatus;
    certainty_level: "definite" | "probable" | "possible" | "unknown";
  };
  confidence: ConfidenceDimensions;
  quality: EvidenceQuality;
  metadata: {
    extraction_timestamp: string;
    extraction_model_version: string;
    superseded_by: string | null;
    is_current: boolean;
  };
};

// ─── NORMALIZED EVIDENCE ─────────────────────────────────────────────────────

export type NormalizedEvidence = {
  normalized_id: string;
  evidence_id: string;
  normalized_value: Record<string, unknown>;
  canonical_codes: CanonicalCode[];
  normalization_timestamp: string;
  normalization_model_version: string;
  normalization_confidence: number;
  original_text_preserved: string;
};

export type CanonicalCode = {
  system: string;
  code: string;
  display: string;
  confidence: number;
};

// ─── DUPLICATE DETECTION ─────────────────────────────────────────────────────

export type DuplicateType =
  | "identical_file"
  | "same_document_different_format"
  | "same_fact_same_source"
  | "same_fact_different_sources"
  | "corroboration";

export type DuplicateEvidence = {
  duplicate_id: string;
  evidence_ids: string[];
  duplicate_type: DuplicateType;
  resolution: "merged" | "kept_separate" | "pending";
  resolution_notes: string;
};

// ─── FAILURE MODES ───────────────────────────────────────────────────────────

export type FailureMode =
  | "ocr_hallucination"
  | "missed_text"
  | "wrong_reading_order"
  | "table_corruption"
  | "wrong_section_attribution"
  | "incorrect_classification"
  | "entity_confusion"
  | "normalization_error"
  | "incorrect_negation"
  | "incorrect_date"
  | "wrong_subject"
  | "wrong_speaker"
  | "duplicate_inflation"
  | "evidence_conflation"
  | "unsupported_inference"
  | "loss_of_provenance"
  | "normalization_destroying_source"
  | "confidence_mistaken_for_truth"
  | "low_quality_treated_as_authoritative"
  | "repeated_documentation_as_independent"
  | "partial_document_as_complete";

export type FailureDefense = {
  failure_mode: FailureMode;
  defense_mechanism: string;
  is_active: boolean;
  triggered_count: number;
};

// ─── PROCESSING RESULTS ──────────────────────────────────────────────────────

export type IngestionResult = {
  input: ImmutableInput;
  status: InputStatus;
  parsed_document: ParsedDocument | null;
  evidence_objects: EvidenceObject[];
  normalized_evidence: NormalizedEvidence[];
  duplicates: DuplicateEvidence[];
  failures: ProcessingFailure[];
  processing_timestamp: string;
};

export type ProcessingFailure = {
  failure_mode: FailureMode;
  description: string;
  severity: "critical" | "warning" | "info";
  affected_evidence_ids: string[];
  defense_applied: string;
};
