/**
 * Evidence & Input Intelligence — Failure Mode Defenses
 *
 * Systematic defenses against every identified failure mode.
 * Each defense is a guard that prevents the system from silently
 * converting uncertainty into certainty.
 */

import type {
  EvidenceObject,
  FailureDefense,
  FailureMode,
  ProcessingFailure,
} from "./types";

const failureDefenses = new Map<FailureMode, FailureDefense>();

/**
 * Initialize all failure mode defenses.
 */
export function initializeFailureDefenses(): void {
  const defenses: FailureDefense[] = [
    {
      failure_mode: "ocr_hallucination",
      defense_mechanism: "Character-level confidence threshold; flag low-confidence reads; never fabricate",
      is_active: true,
      triggered_count: 0,
    },
    {
      failure_mode: "missed_text",
      defense_mechanism: "Full document scan; completeness check; flag if extraction seems sparse",
      is_active: true,
      triggered_count: 0,
    },
    {
      failure_mode: "wrong_reading_order",
      defense_mechanism: "Layout analysis with reading order confidence; manual verification path",
      is_active: true,
      triggered_count: 0,
    },
    {
      failure_mode: "table_corruption",
      defense_mechanism: "Table structure validation; flag ambiguous cells",
      is_active: true,
      triggered_count: 0,
    },
    {
      failure_mode: "wrong_section_attribution",
      defense_mechanism: "Section path tracking; validate section context matches extraction",
      is_active: true,
      triggered_count: 0,
    },
    {
      failure_mode: "incorrect_classification",
      defense_mechanism: "Multi-label with confidence; low-confidence triggers generic extraction",
      is_active: true,
      triggered_count: 0,
    },
    {
      failure_mode: "entity_confusion",
      defense_mechanism: "Entity-type validation; cross-reference with context",
      is_active: true,
      triggered_count: 0,
    },
    {
      failure_mode: "normalization_error",
      defense_mechanism: "Preserve original; flag low-confidence normalizations",
      is_active: true,
      triggered_count: 0,
    },
    {
      failure_mode: "incorrect_negation",
      defense_mechanism: "Negation detection model; flag uncertain negation for review",
      is_active: true,
      triggered_count: 0,
    },
    {
      failure_mode: "incorrect_date",
      defense_mechanism: "Date validation; flag implausible dates; preserve relative dates",
      is_active: true,
      triggered_count: 0,
    },
    {
      failure_mode: "wrong_subject",
      defense_mechanism: "Subject tracking across document; flag subject switches",
      is_active: true,
      triggered_count: 0,
    },
    {
      failure_mode: "wrong_speaker",
      defense_mechanism: "Speaker attribution model; preserve quotes distinctly",
      is_active: true,
      triggered_count: 0,
    },
    {
      failure_mode: "duplicate_inflation",
      defense_mechanism: "Same-source same-fact merging; cross-source linking",
      is_active: true,
      triggered_count: 0,
    },
    {
      failure_mode: "evidence_conflation",
      defense_mechanism: "Separate evidence objects for separate statements",
      is_active: true,
      triggered_count: 0,
    },
    {
      failure_mode: "unsupported_inference",
      defense_mechanism: "Validation: does evidence directly paraphrase source?",
      is_active: true,
      triggered_count: 0,
    },
    {
      failure_mode: "loss_of_provenance",
      defense_mechanism: "Mandatory provenance fields; validation rejects evidence without provenance",
      is_active: true,
      triggered_count: 0,
    },
    {
      failure_mode: "normalization_destroying_source",
      defense_mechanism: "Original text always preserved; normalization is additive",
      is_active: true,
      triggered_count: 0,
    },
    {
      failure_mode: "confidence_mistaken_for_truth",
      defense_mechanism: "Separate fields; UI/UX distinction; documentation",
      is_active: true,
      triggered_count: 0,
    },
    {
      failure_mode: "low_quality_treated_as_authoritative",
      defense_mechanism: "Evidence quality score; downstream weights by quality",
      is_active: true,
      triggered_count: 0,
    },
    {
      failure_mode: "repeated_documentation_as_independent",
      defense_mechanism: "Same-source detection; corroboration linking",
      is_active: true,
      triggered_count: 0,
    },
    {
      failure_mode: "partial_document_as_complete",
      defense_mechanism: "Document completeness flag; extraction scoped to available content",
      is_active: true,
      triggered_count: 0,
    },
  ];

  for (const defense of defenses) {
    failureDefenses.set(defense.failure_mode, defense);
  }
}

/**
 * Get all active failure defenses.
 */
export function getFailureDefenses(): FailureDefense[] {
  return [...failureDefenses.values()];
}

/**
 * Trigger a failure defense — records that a failure was detected and handled.
 */
export function triggerFailureDefense(failure_mode: FailureMode): void {
  const defense = failureDefenses.get(failure_mode);
  if (defense) {
    defense.triggered_count += 1;
  }
}

/**
 * Create a processing failure record.
 */
export function createProcessingFailure(params: {
  failure_mode: FailureMode;
  description: string;
  severity: "critical" | "warning" | "info";
  affected_evidence_ids?: string[];
}): ProcessingFailure {
  triggerFailureDefense(params.failure_mode);

  return {
    failure_mode: params.failure_mode,
    description: params.description,
    severity: params.severity,
    affected_evidence_ids: params.affected_evidence_ids ?? [],
    defense_applied: failureDefenses.get(params.failure_mode)?.defense_mechanism ?? "No defense available",
  };
}

/**
 * Validate evidence against all applicable failure defenses.
 * Returns any failures detected.
 */
export function validateAgainstFailureModes(evidence: EvidenceObject): ProcessingFailure[] {
  const failures: ProcessingFailure[] = [];

  // Check: provenance present
  if (!evidence.provenance?.source_location?.text_span) {
    failures.push(
      createProcessingFailure({
        failure_mode: "loss_of_provenance",
        description: "Evidence missing source location text span",
        severity: "critical",
        affected_evidence_ids: [evidence.evidence_id],
      }),
    );
  }

  // Check: original text preserved
  if (!evidence.content.original_text || evidence.content.original_text.trim().length === 0) {
    failures.push(
      createProcessingFailure({
        failure_mode: "normalization_destroying_source",
        description: "Evidence missing original text",
        severity: "critical",
        affected_evidence_ids: [evidence.evidence_id],
      }),
    );
  }

  // Check: negation status is valid
  if (!evidence.negation?.negation_status) {
    failures.push(
      createProcessingFailure({
        failure_mode: "incorrect_negation",
        description: "Evidence missing negation status",
        severity: "warning",
        affected_evidence_ids: [evidence.evidence_id],
      }),
    );
  }

  // Check: confidence not mistaken for truth
  if (evidence.confidence.overall_confidence > 0.9 && evidence.quality.quality_score === "low") {
    failures.push(
      createProcessingFailure({
        failure_mode: "confidence_mistaken_for_truth",
        description: "High confidence but low quality — risk of treating extraction as truth",
        severity: "warning",
        affected_evidence_ids: [evidence.evidence_id],
      }),
    );
  }

  // Check: unsupported inference
  if (evidence.content.value.inferred === true) {
    failures.push(
      createProcessingFailure({
        failure_mode: "unsupported_inference",
        description: "Evidence contains inferred content — should be extracted only",
        severity: "critical",
        affected_evidence_ids: [evidence.evidence_id],
      }),
    );
  }

  return failures;
}

/**
 * Initialize defenses on module load.
 */
initializeFailureDefenses();
