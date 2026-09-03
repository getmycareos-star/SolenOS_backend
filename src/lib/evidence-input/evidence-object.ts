/**
 * Evidence & Input Intelligence — Evidence Object Factory
 *
 * Creates the irreducible evidence object with all required fields.
 * Every evidence object is immutable once created.
 */

import type {
  Attribution,
  ConfidenceDimensions,
  EvidenceObject,
  EvidenceQuality,
  EvidenceType,
  NegationStatus,
  ProvenanceChain,
  SourceLocation,
  Temporality,
} from "./types";

const EXTRACTION_MODEL_VERSION = "evidence-input-v1.0.0";

function generateEvidenceId(): string {
  return `ev_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 10)}`;
}

export type CreateEvidenceObjectParams = {
  provenance: ProvenanceChain;
  attribution: Attribution;
  content_type: EvidenceType;
  content_value: Record<string, unknown>;
  original_text: string;
  temporal_status: Temporality;
  event_date?: string | null;
  date_confidence?: number | null;
  negation_status: NegationStatus;
  certainty_level?: "definite" | "probable" | "possible" | "unknown";
  confidence: ConfidenceDimensions;
  quality: EvidenceQuality;
};

/**
 * Create an immutable evidence object.
 * All required fields must be provided — no shortcuts.
 */
export function createEvidenceObject(params: CreateEvidenceObjectParams): EvidenceObject {
  const now = new Date().toISOString();

  return {
    evidence_id: generateEvidenceId(),
    provenance: params.provenance,
    attribution: params.attribution,
    content: {
      type: params.content_type,
      value: params.content_value,
      original_text: params.original_text,
    },
    temporality: {
      temporal_status: params.temporal_status,
      event_date: params.event_date ?? null,
      date_confidence: params.date_confidence ?? null,
    },
    negation: {
      negation_status: params.negation_status,
      certainty_level: params.certainty_level ?? "unknown",
    },
    confidence: params.confidence,
    quality: params.quality,
    metadata: {
      extraction_timestamp: now,
      extraction_model_version: EXTRACTION_MODEL_VERSION,
      superseded_by: null,
      is_current: true,
    },
  };
}

/**
 * Create a new evidence object that supersedes an old one.
 * The old object is NOT modified — a new one is created.
 */
export function supersedeEvidenceObject(
  old_evidence: EvidenceObject,
  new_params: Partial<CreateEvidenceObjectParams>,
): EvidenceObject {
  const now = new Date().toISOString();

  const new_evidence: EvidenceObject = {
    evidence_id: generateEvidenceId(),
    provenance: new_params.provenance ?? old_evidence.provenance,
    attribution: new_params.attribution ?? old_evidence.attribution,
    content: {
      type: new_params.content_type ?? old_evidence.content.type,
      value: new_params.content_value ?? old_evidence.content.value,
      original_text: new_params.original_text ?? old_evidence.content.original_text,
    },
    temporality: {
      temporal_status: new_params.temporal_status ?? old_evidence.temporality.temporal_status,
      event_date: new_params.event_date ?? old_evidence.temporality.event_date,
      date_confidence: new_params.date_confidence ?? old_evidence.temporality.date_confidence,
    },
    negation: {
      negation_status: new_params.negation_status ?? old_evidence.negation.negation_status,
      certainty_level: new_params.certainty_level ?? old_evidence.negation.certainty_level,
    },
    confidence: new_params.confidence ?? old_evidence.confidence,
    quality: new_params.quality ?? old_evidence.quality,
    metadata: {
      extraction_timestamp: now,
      extraction_model_version: EXTRACTION_MODEL_VERSION,
      superseded_by: null,
      is_current: true,
    },
  };

  return new_evidence;
}

/**
 * Validate that an evidence object has all required fields.
 */
export function validateEvidenceObject(evidence: EvidenceObject): {
  valid: boolean;
  missing_fields: string[];
} {
  const missing_fields: string[] = [];

  if (!evidence.evidence_id) missing_fields.push("evidence_id");
  if (!evidence.provenance) missing_fields.push("provenance");
  if (!evidence.provenance?.original_input_id) missing_fields.push("provenance.original_input_id");
  if (!evidence.provenance?.source_location) missing_fields.push("provenance.source_location");
  if (!evidence.attribution) missing_fields.push("attribution");
  if (!evidence.attribution?.subject) missing_fields.push("attribution.subject");
  if (!evidence.content) missing_fields.push("content");
  if (!evidence.content?.type) missing_fields.push("content.type");
  if (!evidence.content?.original_text) missing_fields.push("content.original_text");
  if (!evidence.temporality) missing_fields.push("temporality");
  if (!evidence.negation) missing_fields.push("negation");
  if (!evidence.confidence) missing_fields.push("confidence");
  if (!evidence.quality) missing_fields.push("quality");
  if (!evidence.metadata) missing_fields.push("metadata");

  return {
    valid: missing_fields.length === 0,
    missing_fields,
  };
}

/**
 * Get the current extraction model version.
 */
export function getExtractionModelVersion(): string {
  return EXTRACTION_MODEL_VERSION;
}
