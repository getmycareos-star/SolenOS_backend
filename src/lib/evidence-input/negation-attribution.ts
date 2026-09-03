/**
 * Evidence & Input Intelligence — Negation and Attribution
 *
 * Critical safety layer: distinguishes what is affirmed from what is negated,
 * and who is making each statement. Without this, extraction is dangerous.
 */

import type {
  Attribution,
  EvidenceObject,
  NegationStatus,
  ReportingType,
  SourceLocation,
} from "./types";

// ─── NEGATION DETECTION ──────────────────────────────────────────────────────

const NEGATION_MARKERS = [
  /\b(?:no\s+evidence\s+of)\b/i,
  /\b(?:does\s+not\s+have)\b/i,
  /\b(?:denies)\b/i,
  /\b(?:denied)\b/i,
  /\b(?:negative\s+for)\b/i,
  /\b(?:ruled\s+out)\b/i,
  /\b(?:rule\s+out)\b/i,
  /\b(?:without)\b/i,
  /\b(?:absence\s+of)\b/i,
  /\b(?:no\s+signs?\s+of)\b/i,
  /\b(?:no\s+symptoms?\s+of)\b/i,
  /\b(?:no)\b/i,
  /\b(?:not)\b/i,
  /\b(?:never)\b/i,
  /\b(?:free\s+of)\b/i,
] as const;

const SUSPECTED_MARKERS = [
  /\b(?:concern\s+for)\b/i,
  /\b(?:suspicious\s+for)\b/i,
  /\b(?:possible)\b/i,
  /\b(?:possibly)\b/i,
  /\b(?:may\s+have)\b/i,
  /\b(?:might\s+have)\b/i,
  /\b(?:could\s+be)\b/i,
  /\b(?:suspect)\b/i,
  /\b(?:suspected)\b/i,
  /\b(?:questionable)\b/i,
  /\b(?:vs\.?)\b/i,
  /\b(?:versus)\b/i,
  /\b(?:differential)\b/i,
  /\b(?:consider)\b/i,
  /\b(?:worried\s+about)\b/i,
  /\b(?:worrisome\s+for)\b/i,
] as const;

const HISTORY_MARKERS = [
  /\b(?:history\s+of)\b/i,
  /\b(?:previous(?:ly)?)\b/i,
  /\b(?:past)\b/i,
  /\b(?:former(?:ly)?)\b/i,
  /\b(?:was\s+diagnosed\s+with)\b/i,
  /\b(?:had)\b/i,
  /\b(?:prior)\b/i,
] as const;

const FAMILY_HISTORY_MARKERS = [
  /\b(?:family\s+history\s+of)\b/i,
  /\b(?:mother\s+had)\b/i,
  /\b(?:father\s+had)\b/i,
  /\b(?:sibling\s+has)\b/i,
  /\b(?:brother\s+has)\b/i,
  /\b(?:sister\s+has)\b/i,
  /\b(?:maternal)\b/i,
  /\b(?:paternal)\b/i,
] as const;

const RESOLVED_MARKERS = [
  /\b(?:resolved)\b/i,
  /\b(?:cured)\b/i,
  /\b(?:cleared)\b/i,
  /\b(?:healed)\b/i,
  /\b(?:recovered\s+from)\b/i,
] as const;

const RULE_OUT_MARKERS = [
  /\b(?:ruled\s+out)\b/i,
  /\b(?:rule\s+out)\b/i,
  /\b(?:exclude)\b/i,
  /\b(?:excluded)\b/i,
  /\b(?:r\/o)\b/i,
] as const;

/**
 * Detect the negation status of a text span.
 * Returns the detected status and confidence level.
 */
export function detectNegation(text: string): {
  status: NegationStatus;
  confidence: "definite" | "probable" | "possible" | "unknown";
} {
  const normalized = text.toLowerCase().trim();

  // Check for family history first (highest priority - changes subject)
  for (const pattern of FAMILY_HISTORY_MARKERS) {
    if (pattern.test(normalized)) {
      return { status: "family_history_of", confidence: "definite" };
    }
  }

  // Check for resolved status
  for (const pattern of RESOLVED_MARKERS) {
    if (pattern.test(normalized)) {
      return { status: "resolved", confidence: "definite" };
    }
  }

  // Check for ruled out
  for (const pattern of RULE_OUT_MARKERS) {
    if (pattern.test(normalized)) {
      return { status: "ruled_out", confidence: "definite" };
    }
  }

  // Check for history
  for (const pattern of HISTORY_MARKERS) {
    if (pattern.test(normalized)) {
      return { status: "history_of", confidence: "probable" };
    }
  }

  // Check for suspected
  for (const pattern of SUSPECTED_MARKERS) {
    if (pattern.test(normalized)) {
      return { status: "suspected", confidence: "probable" };
    }
  }

  // Check for negation
  for (const pattern of NEGATION_MARKERS) {
    if (pattern.test(normalized)) {
      return { status: "negated", confidence: "probable" };
    }
  }

  return { status: "affirmed", confidence: "possible" };
}

// ─── ATTRIBUTION DETECTION ────────────────────────────────────────────────────

const REPORTED_BY_MARKERS: Array<{ pattern: RegExp; speaker: string }> = [
  { pattern: /\b(?:daughter\s+reports?)\b/i, speaker: "daughter" },
  { pattern: /\b(?:son\s+reports?)\b/i, speaker: "son" },
  { pattern: /\b(?:wife\s+reports?)\b/i, speaker: "wife" },
  { pattern: /\b(?:husband\s+reports?)\b/i, speaker: "husband" },
  { pattern: /\b(?:caregiver\s+reports?)\b/i, speaker: "caregiver" },
  { pattern: /\b(?:mother\s+reports?)\b/i, speaker: "mother" },
  { pattern: /\b(?:father\s+reports?)\b/i, speaker: "father" },
  { pattern: /\b(?:family\s+reports?)\b/i, speaker: "family" },
  { pattern: /\b(?:patient\s+reports?)\b/i, speaker: "patient" },
  { pattern: /\b(?:patient\s+states?)\b/i, speaker: "patient" },
  { pattern: /\b(?:patient\s+says?)\b/i, speaker: "patient" },
  { pattern: /\b(?:according\s+to\s+(?:the\s+)?patient)\b/i, speaker: "patient" },
  { pattern: /\b(?:nurse\s+reports?)\b/i, speaker: "nurse" },
  { pattern: /\b(?:doctor\s+reports?)\b/i, speaker: "doctor" },
  { pattern: /\b(?:physician\s+reports?)\b/i, speaker: "physician" },
];

const OBSERVATION_MARKERS = [
  /\b(?:observed)\b/i,
  /\b(?:noted)\b/i,
  /\b(?:measured)\b/i,
  /\b(?:found)\b/i,
  /\b(?:revealed)\b/i,
  /\b(?:showed)\b/i,
  /\b(?:shows?)\b/i,
  /\b(?:demonstrated)\b/i,
] as const;

/**
 * Detect attribution — who is making this statement.
 */
export function detectAttribution(text: string, default_subject: string = "patient"): Attribution {
  const normalized = text.toLowerCase().trim();

  // Check for reported speech
  for (const { pattern, speaker } of REPORTED_BY_MARKERS) {
    if (pattern.test(normalized)) {
      return {
        author: null,
        speaker,
        subject: default_subject,
        reporting_type: "caregiver_reported",
      };
    }
  }

  // Check for direct observation
  for (const pattern of OBSERVATION_MARKERS) {
    if (pattern.test(normalized)) {
      return {
        author: null,
        speaker: null,
        subject: default_subject,
        reporting_type: "direct_observation",
      };
    }
  }

  // Default: documented in record
  return {
    author: null,
    speaker: null,
    subject: default_subject,
    reporting_type: "documented_in_record",
  };
}

// ─── SOURCE LOCATION ─────────────────────────────────────────────────────────

/**
 * Create a source location that preserves exactly where evidence came from.
 */
export function createSourceLocation(params: {
  input_id: string;
  document_id: string;
  page_number?: number | null;
  region_id?: string | null;
  text_span: string;
  section_path?: string[];
  bounding_box?: { x: number; y: number; width: number; height: number } | null;
}): SourceLocation {
  return {
    input_id: params.input_id,
    document_id: params.document_id,
    page_number: params.page_number ?? null,
    region_id: params.region_id ?? null,
    text_span: params.text_span,
    section_path: params.section_path ?? [],
    bounding_box: params.bounding_box ?? null,
  };
}
