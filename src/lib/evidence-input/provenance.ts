/**
 * Evidence & Input Intelligence — Provenance Chain
 *
 * Every evidence object must answer: "Show me exactly where this came from."
 * The provenance chain provides this audit trail from original input to
 * extracted evidence.
 */

import type {
  ProvenanceChain,
  SourceLocation,
  TransformationStep,
} from "./types";

/**
 * Create a provenance chain linking evidence back to its original input.
 */
export function createProvenanceChain(params: {
  input_id: string;
  ingestion_timestamp: string;
  source_location: SourceLocation;
  parse_model_version?: string;
  extraction_model_version?: string;
  additional_steps?: TransformationStep[];
}): ProvenanceChain {
  const now = new Date().toISOString();

  const defaultSteps: TransformationStep[] = [
    {
      step: "ingestion",
      timestamp: params.ingestion_timestamp,
      model_version: "input-storage-v1",
      input_description: "Original file bytes",
      output_description: "Immutable input stored with hash",
    },
    {
      step: "parsing",
      timestamp: now,
      model_version: params.parse_model_version ?? "parser-v1",
      input_description: "Original file bytes",
      output_description: "Parsed document with layout and reading order",
    },
    {
      step: "extraction",
      timestamp: now,
      model_version: params.extraction_model_version ?? "extractor-v1",
      input_description: "Parsed document regions",
      output_description: "Structured evidence objects",
    },
  ];

  return {
    original_input_id: params.input_id,
    ingestion_timestamp: params.ingestion_timestamp,
    parse_timestamp: now,
    extraction_timestamp: now,
    parse_model_version: params.parse_model_version ?? "parser-v1",
    extraction_model_version: params.extraction_model_version ?? "extractor-v1",
    source_location: params.source_location,
    transformation_steps: [
      ...defaultSteps,
      ...(params.additional_steps ?? []),
    ],
  };
}

/**
 * Verify that a provenance chain is complete and valid.
 */
export function verifyProvenanceChain(chain: ProvenanceChain): {
  valid: boolean;
  issues: string[];
} {
  const issues: string[] = [];

  if (!chain.original_input_id) {
    issues.push("Missing original input ID");
  }
  if (!chain.ingestion_timestamp) {
    issues.push("Missing ingestion timestamp");
  }
  if (!chain.source_location) {
    issues.push("Missing source location");
  }
  if (!chain.source_location?.text_span) {
    issues.push("Missing text span in source location");
  }
  if (!chain.transformation_steps || chain.transformation_steps.length === 0) {
    issues.push("No transformation steps recorded");
  }

  // Verify chain continuity
  const steps = chain.transformation_steps;
  for (let i = 1; i < steps.length; i++) {
    const prev = steps[i - 1];
    const curr = steps[i];
    if (new Date(prev.timestamp) > new Date(curr.timestamp)) {
      issues.push(`Step ${i} timestamp is before step ${i - 1}`);
    }
  }

  return {
    valid: issues.length === 0,
    issues,
  };
}

/**
 * Format a provenance chain for human-readable display.
 */
export function formatProvenanceChain(chain: ProvenanceChain): string {
  const lines: string[] = [];
  lines.push("=== EVIDENCE PROVENANCE CHAIN ===");
  lines.push(`Original Input: ${chain.original_input_id}`);
  lines.push(`Ingested: ${chain.ingestion_timestamp}`);
  lines.push(`Source Location: Page ${chain.source_location.page_number ?? "unknown"}, Region ${chain.source_location.region_id ?? "unknown"}`);
  lines.push(`Text Span: "${chain.source_location.text_span.slice(0, 100)}${chain.source_location.text_span.length > 100 ? "..." : ""}"`);
  lines.push("");
  lines.push("Transformation Steps:");
  for (const step of chain.transformation_steps) {
    lines.push(`  [${step.step}] ${step.timestamp} (${step.model_version})`);
    lines.push(`    Input: ${step.input_description}`);
    lines.push(`    Output: ${step.output_description}`);
  }
  lines.push("=================================");
  return lines.join("\n");
}
