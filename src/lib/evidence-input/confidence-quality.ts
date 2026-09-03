/**
 * Evidence & Input Intelligence — Multi-Dimensional Confidence & Quality
 *
 * Separates extraction confidence (how sure is the system)
 * from evidence quality (how reliable is the source).
 * These must never be confused.
 */

import type {
  ConfidenceDimensions,
  EvidenceObject,
  EvidenceQuality,
  SourceLocation,
} from "./types";

// ─── CONFIDENCE COMPUTATION ──────────────────────────────────────────────────

/**
 * Compute multi-dimensional confidence scores.
 * Each dimension is scored independently — they are NOT the same thing.
 */
export function computeConfidence(params: {
  ocr_confidence?: number | null;
  parse_confidence: number;
  extraction_confidence: number;
  entity_normalization_confidence?: number | null;
  temporal_extraction_confidence?: number | null;
  negation_detection_confidence: number;
}): ConfidenceDimensions {
  const {
    ocr_confidence = null,
    parse_confidence,
    extraction_confidence,
    entity_normalization_confidence = null,
    temporal_extraction_confidence = null,
    negation_detection_confidence,
  } = params;

  // Overall confidence is weighted average of available dimensions
  let totalWeight = 0;
  let weightedSum = 0;

  // Parse confidence (always present)
  weightedSum += parse_confidence * 0.2;
  totalWeight += 0.2;

  // Extraction confidence (always present, highest weight)
  weightedSum += extraction_confidence * 0.35;
  totalWeight += 0.35;

  // Negation detection confidence (always present)
  weightedSum += negation_detection_confidence * 0.15;
  totalWeight += 0.15;

  // OCR confidence (only if applicable)
  if (ocr_confidence !== null) {
    weightedSum += ocr_confidence * 0.15;
    totalWeight += 0.15;
  }

  // Entity normalization confidence (only if applicable)
  if (entity_normalization_confidence !== null) {
    weightedSum += entity_normalization_confidence * 0.1;
    totalWeight += 0.1;
  }

  // Temporal extraction confidence (only if applicable)
  if (temporal_extraction_confidence !== null) {
    weightedSum += temporal_extraction_confidence * 0.05;
    totalWeight += 0.05;
  }

  const overall_confidence = totalWeight > 0 ? weightedSum / totalWeight : extraction_confidence;

  return {
    ocr_confidence,
    parse_confidence,
    extraction_confidence,
    entity_normalization_confidence,
    temporal_extraction_confidence,
    negation_detection_confidence,
    overall_confidence: Math.round(overall_confidence * 100) / 100,
  };
}

// ─── EVIDENCE QUALITY ASSESSMENT ─────────────────────────────────────────────

/**
 * Assess evidence quality — the inherent reliability of the source evidence,
 * independent of extraction accuracy.
 *
 * A perfectly extracted blurry caregiver note has high extraction confidence
 * but low evidence quality.
 */
export function assessEvidenceQuality(params: {
  source_type: string;
  is_complete: boolean;
  legibility: "clear" | "degraded" | "illegible";
  reporting_type: string;
  document_age_days?: number;
  specificity: "specific" | "vague";
  additional_notes?: string[];
}): EvidenceQuality {
  const {
    source_type,
    is_complete,
    legibility,
    reporting_type,
    document_age_days = 0,
    specificity,
    additional_notes = [],
  } = params;

  // Source type reliability
  let source_type_reliability: EvidenceQuality["source_type_reliability"] = "unknown";
  if (/\b(?:lab_result|signed_report|official_record|discharge_summary)\b/i.test(source_type)) {
    source_type_reliability = "authoritative";
  } else if (/\b(?:clinical_note|physician_note|specialist_note)\b/i.test(source_type)) {
    source_type_reliability = "professional";
  } else if (/\b(?:caregiver_note|family_report|patient_report)\b/i.test(source_type)) {
    source_type_reliability = "caregiver";
  }

  // Completeness
  const completeness: EvidenceQuality["completeness"] = is_complete ? "complete" : "partial";

  // Directness
  let directness: EvidenceQuality["directness"] = "reported";
  if (reporting_type === "direct_observation") {
    directness = "direct";
  } else if (reporting_type === "family_reported" || reporting_type === "caregiver_reported") {
    directness = "hearsay";
  }

  // Timeliness
  let timeliness: EvidenceQuality["timeliness"] = "unknown";
  if (document_age_days <= 30) {
    timeliness = "current";
  } else if (document_age_days > 365) {
    timeliness = "outdated";
  } else {
    timeliness = "current";
  }

  // Compute overall quality score
  let quality_points = 0;
  let max_points = 0;

  // Source reliability (0-3 points)
  max_points += 3;
  if (source_type_reliability === "authoritative") quality_points += 3;
  else if (source_type_reliability === "professional") quality_points += 2;
  else if (source_type_reliability === "caregiver") quality_points += 1;

  // Legibility (0-2 points)
  max_points += 2;
  if (legibility === "clear") quality_points += 2;
  else if (legibility === "degraded") quality_points += 1;

  // Completeness (0-2 points)
  max_points += 2;
  if (completeness === "complete") quality_points += 2;
  else if (completeness === "partial") quality_points += 1;

  // Directness (0-2 points)
  max_points += 2;
  if (directness === "direct") quality_points += 2;
  else if (directness === "reported") quality_points += 1;

  // Specificity (0-1 point)
  max_points += 1;
  if (specificity === "specific") quality_points += 1;

  const quality_ratio = max_points > 0 ? quality_points / max_points : 0.5;
  let quality_score: EvidenceQuality["quality_score"] = "unknown";
  if (quality_ratio >= 0.7) quality_score = "high";
  else if (quality_ratio >= 0.4) quality_score = "medium";
  else quality_score = "low";

  return {
    quality_score,
    source_type_reliability,
    completeness,
    legibility,
    directness,
    timeliness,
    specificity,
    quality_notes: additional_notes,
  };
}

// ─── CONFIDENCE VS QUALITY DISTINCTION ────────────────────────────────────────

/**
 * Verify that confidence and quality are not being confused.
 * Returns a warning if they appear conflated.
 */
export function verifyConfidenceQualitySeparation(evidence: EvidenceObject): {
  properly_separated: boolean;
  warnings: string[];
} {
  const warnings: string[] = [];
  const properly_separated = true;

  // Check: high confidence + low quality should not be treated as authoritative
  if (evidence.confidence.overall_confidence >= 0.8 && evidence.quality.quality_score === "low") {
    warnings.push(
      "High extraction confidence but low evidence quality — do not treat as authoritative"
    );
  }

  // Check: low confidence + high quality should not be discarded
  if (evidence.confidence.overall_confidence < 0.5 && evidence.quality.quality_score === "high") {
    warnings.push(
      "Low extraction confidence but high evidence quality — verify extraction manually"
    );
  }

  return { properly_separated, warnings };
}
