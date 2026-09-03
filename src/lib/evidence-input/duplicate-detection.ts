/**
 * Evidence & Input Intelligence — Duplicate Detection
 *
 * Distinguishes between:
 * - Same file uploaded twice (deduplicate)
 * - Same fact stated multiple times in same source (merge)
 * - Same fact stated in different sources (keep separate, link as corroboration)
 *
 * Three documents repeating the same fact does NOT mean three independent
 * pieces of evidence. This module handles that distinction.
 */

import type {
  DuplicateEvidence,
  DuplicateType,
  EvidenceObject,
} from "./types";

const duplicateStore = new Map<string, DuplicateEvidence>();

function generateDuplicateId(): string {
  return `dup_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
}

/**
 * Compute a fingerprint for an evidence object.
 * Used to detect potential duplicates.
 */
export function computeEvidenceFingerprint(evidence: EvidenceObject): string {
  const components = [
    evidence.content.type,
    evidence.content.original_text.toLowerCase().trim(),
    evidence.negation.negation_status,
    evidence.temporality.temporal_status,
    evidence.attribution.subject,
  ];
  return components.join("|");
}

/**
 * Detect potential duplicates among a set of evidence objects.
 */
export function detectDuplicates(
  evidence_objects: EvidenceObject[],
): DuplicateEvidence[] {
  const duplicates: DuplicateEvidence[] = [];
  const fingerprint_map = new Map<string, EvidenceObject[]>();

  // Group by fingerprint
  for (const evidence of evidence_objects) {
    const fp = computeEvidenceFingerprint(evidence);
    const existing = fingerprint_map.get(fp) ?? [];
    existing.push(evidence);
    fingerprint_map.set(fp, existing);
  }

  // Identify groups with duplicates
  for (const [fp, group] of fingerprint_map) {
    if (group.length < 2) continue;

    // Determine duplicate type
    const type = classifyDuplicateType(group);

    const dup: DuplicateEvidence = {
      duplicate_id: generateDuplicateId(),
      evidence_ids: group.map((e) => e.evidence_id),
      duplicate_type: type,
      resolution: type === "identical_file" ? "merged" : "kept_separate",
      resolution_notes: generateResolutionNotes(type, group),
    };

    duplicates.push(dup);
    duplicateStore.set(dup.duplicate_id, dup);
  }

  return duplicates;
}

/**
 * Classify the type of duplicate.
 */
function classifyDuplicateType(group: EvidenceObject[]): DuplicateType {
  // Check if same input (same source)
  const input_ids = new Set(group.map((e) => e.provenance.original_input_id));
  if (input_ids.size === 1) {
    return "same_fact_same_source";
  }

  // Check if same text span (identical content)
  const text_spans = new Set(group.map((e) => e.provenance.source_location.text_span));
  if (text_spans.size === 1) {
    return "same_fact_different_sources";
  }

  // Different sources, similar content = corroboration
  return "corroboration";
}

/**
 * Generate human-readable resolution notes.
 */
function generateResolutionNotes(type: DuplicateType, group: EvidenceObject[]): string {
  switch (type) {
    case "identical_file":
      return `Same file uploaded multiple times. Merged into single evidence set.`;
    case "same_document_different_format":
      return `Same document in different formats. Linked as same source.`;
    case "same_fact_same_source":
      return `Same fact stated multiple times in same source (${group[0]?.provenance.original_input_id}). Merged.`;
    case "same_fact_different_sources":
      return `Same fact stated in ${group.length} different sources. Kept separate as independent evidence.`;
    case "corroboration":
      return `Similar fact from ${group.length} independent sources. Linked as corroboration.`;
    default:
      return `Duplicate detected. Resolution pending.`;
  }
}

/**
 * Check if two evidence objects are duplicates.
 */
export function areDuplicates(a: EvidenceObject, b: EvidenceObject): boolean {
  return computeEvidenceFingerprint(a) === computeEvidenceFingerprint(b);
}

/**
 * Get all detected duplicates.
 */
export function getAllDuplicates(): DuplicateEvidence[] {
  return [...duplicateStore.values()];
}

/**
 * Clear duplicate store (for testing).
 */
export function clearDuplicateStore(): void {
  duplicateStore.clear();
}
