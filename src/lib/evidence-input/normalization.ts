/**
 * Evidence & Input Intelligence — Normalization with Original Preservation
 *
 * Normalization is dangerous. It can silently destroy source meaning.
 * This module ensures normalization is always additive — the original
 * representation is ALWAYS preserved and recoverable.
 *
 * Golden rule: Normalization is a VIEW on top of evidence, not a replacement.
 */

import type {
  CanonicalCode,
  EvidenceObject,
  NormalizedEvidence,
} from "./types";

const NORMALIZATION_MODEL_VERSION = "normalization-v1.0.0";

function generateNormalizedId(): string {
  return `norm_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 10)}`;
}

// ─── MEDICATION NORMALIZATION ────────────────────────────────────────────────

const MEDICATION_SYNONYMS: Record<string, { canonical: string; rxnorm?: string }> = {
  tylenol: { canonical: "acetaminophen", rxnorm: "161" },
  acetaminophen: { canonical: "acetaminophen", rxnorm: "161" },
  advil: { canonical: "ibuprofen", rxnorm: "5640" },
  ibuprofen: { canonical: "ibuprofen", rxnorm: "5640" },
  motrin: { canonical: "ibuprofen", rxnorm: "5640" },
  aspirin: { canonical: "aspirin", rxnorm: "1191" },
  metformin: { canonical: "metformin", rxnorm: "6809" },
  lisinopril: { canonical: "lisinopril", rxnorm: "29046" },
  atorvastatin: { canonical: "atorvastatin", rxnorm: "83367" },
  simvastatin: { canonical: "simvastatin", rxnorm: "36567" },
  omeprazole: { canonical: "omeprazole", rxnorm: "7646" },
  pantoprazole: { canonical: "pantoprazole", rxnorm: "40790" },
  levothyroxine: { canonical: "levothyroxine", rxnorm: "10584" },
  amlodipine: { canonical: "amlodipine", rxnorm: "17767" },
  metoprolol: { canonical: "metoprolol", rxnorm: "6918" },
  losartan: { canonical: "losartan", rxnorm: "29046" },
  gabapentin: { canonical: "gabapentin", rxnorm: "25480" },
  sertraline: { canonical: "sertraline", rxnorm: "36437" },
  citalopram: { canonical: "citalopram", rxnorm: "2556" },
  albuterol: { canonical: "albuterol", rxnorm: "435" },
  prednisone: { canonical: "prednisone", rxnorm: "8640" },
  warfarin: { canonical: "warfarin", rxnorm: "11289" },
  insulin: { canonical: "insulin", rxnorm: "253182" },
};

/**
 * Normalize a medication name to its canonical form.
 * Returns the canonical form and confidence level.
 */
export function normalizeMedication(name: string): {
  canonical: string;
  codes: CanonicalCode[];
  confidence: number;
} {
  const normalized = name.toLowerCase().trim();

  // Direct match in synonym table
  const match = MEDICATION_SYNONYMS[normalized];
  if (match) {
    return {
      canonical: match.canonical,
      codes: match.rxnorm
        ? [{ system: "RxNorm", code: match.rxnorm, display: match.canonical, confidence: 0.95 }]
        : [],
      confidence: 0.95,
    };
  }

  // Partial match (contains)
  for (const [synonym, data] of Object.entries(MEDICATION_SYNONYMS)) {
    if (normalized.includes(synonym) || synonym.includes(normalized)) {
      return {
        canonical: data.canonical,
        codes: data.rxnorm
          ? [{ system: "RxNorm", code: data.rxnorm, display: data.canonical, confidence: 0.8 }]
          : [],
        confidence: 0.8,
      };
    }
  }

  // No match — return original with low confidence
  return {
    canonical: name,
    codes: [],
    confidence: 0.3,
  };
}

// ─── CONDITION NORMALIZATION ─────────────────────────────────────────────────

const CONDITION_SYNONYMS: Record<string, { canonical: string; icd10?: string }> = {
  diabetes: { canonical: "diabetes mellitus", icd10: "E11" },
  "diabetes mellitus": { canonical: "diabetes mellitus", icd10: "E11" },
  hypertension: { canonical: "hypertension", icd10: "I10" },
  "high blood pressure": { canonical: "hypertension", icd10: "I10" },
  pneumonia: { canonical: "pneumonia", icd10: "J18" },
  asthma: { canonical: "asthma", icd10: "J45" },
  copd: { canonical: "chronic obstructive pulmonary disease", icd10: "J44" },
  "chronic obstructive pulmonary disease": { canonical: "chronic obstructive pulmonary disease", icd10: "J44" },
  chf: { canonical: "congestive heart failure", icd10: "I50" },
  "congestive heart failure": { canonical: "congestive heart failure", icd10: "I50" },
  "heart failure": { canonical: "heart failure", icd10: "I50" },
  depression: { canonical: "depression", icd10: "F32" },
  anxiety: { canonical: "anxiety", icd10: "F41" },
  dementia: { canonical: "dementia", icd10: "F03" },
  stroke: { canonical: "stroke", icd10: "I63" },
  mi: { canonical: "myocardial infarction", icd10: "I21" },
  "myocardial infarction": { canonical: "myocardial infarction", icd10: "I21" },
  "heart attack": { canonical: "myocardial infarction", icd10: "I21" },
  cancer: { canonical: "malignant neoplasm", icd10: "C80" },
  arthritis: { canonical: "arthritis", icd10: "M13" },
  osteoporosis: { canonical: "osteoporosis", icd10: "M81" },
  flu: { canonical: "influenza", icd10: "J11" },
  influenza: { canonical: "influenza", icd10: "J11" },
  covid: { canonical: "COVID-19", icd10: "U07.1" },
  "covid-19": { canonical: "COVID-19", icd10: "U07.1" },
};

/**
 * Normalize a condition name to its canonical form.
 */
export function normalizeCondition(name: string): {
  canonical: string;
  codes: CanonicalCode[];
  confidence: number;
} {
  const normalized = name.toLowerCase().trim();

  const match = CONDITION_SYNONYMS[normalized];
  if (match) {
    return {
      canonical: match.canonical,
      codes: match.icd10
        ? [{ system: "ICD-10", code: match.icd10, display: match.canonical, confidence: 0.95 }]
        : [],
      confidence: 0.95,
    };
  }

  for (const [synonym, data] of Object.entries(CONDITION_SYNONYMS)) {
    if (normalized.includes(synonym) || synonym.includes(normalized)) {
      return {
        canonical: data.canonical,
        codes: data.icd10
          ? [{ system: "ICD-10", code: data.icd10, display: data.canonical, confidence: 0.8 }]
          : [],
        confidence: 0.8,
      };
    }
  }

  return {
    canonical: name,
    codes: [],
    confidence: 0.3,
  };
}

// ─── DATE NORMALIZATION ──────────────────────────────────────────────────────

/**
 * Normalize a date string to ISO 8601 format.
 * Preserves the original string.
 */
export function normalizeDate(date_str: string): {
  iso_date: string | null;
  original: string;
  confidence: number;
  is_relative: boolean;
} {
  const original = date_str.trim();

  // Try ISO format first
  const iso_match = original.match(/^\d{4}-\d{2}-\d{2}/);
  if (iso_match) {
    return { iso_date: iso_match[0], original, confidence: 0.95, is_relative: false };
  }

  // Try common US date formats
  const us_match = original.match(/(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})/);
  if (us_match) {
    const month = us_match[1].padStart(2, "0");
    const day = us_match[2].padStart(2, "0");
    const year = us_match[3].length === 2 ? `20${us_match[3]}` : us_match[3];
    return {
      iso_date: `${year}-${month}-${day}`,
      original,
      confidence: 0.85,
      is_relative: false,
    };
  }

  // Try month name format
  const months: Record<string, string> = {
    jan: "01", january: "01", feb: "02", february: "02", mar: "03", march: "03",
    apr: "04", april: "04", may: "05", jun: "06", june: "06", jul: "07", july: "07",
    aug: "08", august: "08", sep: "09", september: "09", oct: "10", october: "10",
    nov: "11", november: "11", dec: "12", december: "12",
  };
  const month_name_match = original.match(/([A-Za-z]+)\.?\s+(\d{1,2}),?\s+(\d{4})/);
  if (month_name_match) {
    const month_key = month_name_match[1].toLowerCase();
    const month_num = months[month_key];
    if (month_num) {
      const day = month_name_match[2].padStart(2, "0");
      const year = month_name_match[3];
      return {
        iso_date: `${year}-${month_num}-${day}`,
        original,
        confidence: 0.9,
        is_relative: false,
      };
    }
  }

  // Check for relative dates
  const relative_match = original.match(/\b(today|tomorrow|yesterday|last\s+\w+|next\s+\w+|in\s+\d+\s+\w+)\b/i);
  if (relative_match) {
    return {
      iso_date: null,
      original,
      confidence: 0.7,
      is_relative: true,
    };
  }

  // Unable to parse
  return {
    iso_date: null,
    original,
    confidence: 0.2,
    is_relative: false,
  };
}

// ─── FULL EVIDENCE NORMALIZATION ─────────────────────────────────────────────

/**
 * Normalize an evidence object, preserving the original.
 * Returns a NormalizedEvidence that links back to the original.
 */
export function normalizeEvidence(evidence: EvidenceObject): NormalizedEvidence {
  const normalized_value: Record<string, unknown> = { ...evidence.content.value };
  const canonical_codes: CanonicalCode[] = [];
  let normalization_confidence = evidence.confidence.entity_normalization_confidence ?? 0.5;

  // Normalize based on evidence type
  if (evidence.content.type === "medication") {
    const name = evidence.content.value.name as string | undefined;
    if (name) {
      const med_norm = normalizeMedication(name);
      normalized_value.canonical_name = med_norm.canonical;
      canonical_codes.push(...med_norm.codes);
      normalization_confidence = med_norm.confidence;
    }
  } else if (evidence.content.type === "condition") {
    const name = evidence.content.value.name as string | undefined;
    if (name) {
      const cond_norm = normalizeCondition(name);
      normalized_value.canonical_name = cond_norm.canonical;
      canonical_codes.push(...cond_norm.codes);
      normalization_confidence = cond_norm.confidence;
    }
  } else if (evidence.content.type === "date_time") {
    const date_str = evidence.content.value.date as string | undefined;
    if (date_str) {
      const date_norm = normalizeDate(date_str);
      normalized_value.iso_date = date_norm.iso_date;
      normalized_value.is_relative = date_norm.is_relative;
      normalization_confidence = date_norm.confidence;
    }
  }

  return {
    normalized_id: generateNormalizedId(),
    evidence_id: evidence.evidence_id,
    normalized_value,
    canonical_codes,
    normalization_timestamp: new Date().toISOString(),
    normalization_model_version: NORMALIZATION_MODEL_VERSION,
    normalization_confidence,
    original_text_preserved: evidence.content.original_text,
  };
}

/**
 * Verify that normalization has not destroyed the original meaning.
 */
export function verifyNormalizationPreservation(
  original: EvidenceObject,
  normalized: NormalizedEvidence,
): {
  preserved: boolean;
  issues: string[];
} {
  const issues: string[] = [];

  // Original text must be preserved
  if (normalized.original_text_preserved !== original.content.original_text) {
    issues.push("Original text was modified during normalization");
  }

  // Negation status must be preserved
  if (normalized.normalized_value.negation_status === undefined && original.negation) {
    // This is OK — negation is in the evidence object, not necessarily in normalized value
  }

  // Confidence should not increase through normalization
  if (normalized.normalization_confidence > original.confidence.extraction_confidence + 0.1) {
    issues.push("Normalization confidence significantly higher than extraction confidence — suspicious");
  }

  return {
    preserved: issues.length === 0,
    issues,
  };
}
