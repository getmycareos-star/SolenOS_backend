/**
 * Dementia-Care Context — activation model and strength.
 *
 * This module answers: "Should SolenOS interpret observations in a
 * dementia-care context, and at what strength?"
 *
 * Key invariants:
 *   - No diagnosis required to activate. A `concern_only` or
 *     `under_investigation` context still applies the classifier, with
 *     lower confidence and stronger qualification.
 *   - `documented_subtype` is stored verbatim from source, never inferred
 *     from observations.
 *   - The context may be `none` (no dementia-care context active).
 *
 * This is the *only* place where "dementia" enters the data model as a
 * label. It is never used to convert observations into diagnosis — the
 * firewall in qualification-firewall.ts ensures this.
 */

import { z } from "zod";
import type {
  ContextStrength,
  DementiaCareContext,
  Provenance,
  SourceType,
} from "./types";
import { DementiaCareContextSchema } from "./types";

// ─── Activation Triggers ──────────────────────────────────────────────────

export type ActivationTrigger =
  | { kind: "documented_diagnosis"; source_text: string; provenance: Provenance }
  | { kind: "suspected_impairment"; source_text: string; provenance: Provenance }
  | { kind: "caregiver_concern"; source_text: string; provenance: Provenance }
  | { kind: "active_workflow"; workflow_id: string }
  | { kind: "no_activation" };

/**
 * Patterns that, when matched in source text, indicate a documented
 * dementia diagnosis. Used to *record* the diagnosis quote. Never used
 * to infer or assert the diagnosis.
 *
 * These are detection patterns for activation, NOT inference patterns.
 */
export const DIAGNOSIS_ACTIVATION_PATTERNS: readonly RegExp[] = [
  /\b(?:diagnos(?:ed|is)\s+with|has\s+(?:alzheimer(?:'s)?|dementia|lewy\s+body|frontotemporal|vascular)|(?:alzheimer(?:'s)?|dementia)\s+diagnosis)\b/i,
];

export const SUSPECTED_IMPAIRMENT_PATTERNS: readonly RegExp[] = [
  /\b(?:suspect(?:ed|s)?|possible|likely|cognitive (?:impairment|decline|issues)|memory (?:problems|issues|concerns)|may\s+have|early\s+(?:signs?|stage))\b/i,
];

export const CAREGIVER_CONCERN_PATTERNS: readonly RegExp[] = [
  /\b(?:I(?:'m| am)\s+(?:worried|concerned)|(?:she|he|they)\s+(?:seem|seems|seemed)|not\s+himself|not\s+herself|getting\s+worse|getting\s+confused|memory\s+is\s+(?:bad|getting|slipping))\b/i,
];

/**
 * Pattern for detecting an active cognitive-care workflow. Matched
 * against workflow identifiers or task names, not free text.
 */
export function isActiveCognitiveCareWorkflow(workflowId: string): boolean {
  return /\b(dementia|cognitive|memory|neuro)\b/i.test(workflowId);
}

// ─── Context Builder ──────────────────────────────────────────────────────

export type BuildContextInput = {
  context_id: string;
  subject_id: string;
  /** Current observed state — fed by the upstream state engine */
  documented_diagnosis_quote: string | null;
  documented_subtype: string | null;
  suspected_impairment_signals: string[];
  caregiver_concern_signals: string[];
  active_workflow_ids: string[];
  pending_evaluation: boolean;
  /** Any prior provenance to attach to diagnosis quote */
  diagnosis_quote_provenance: Provenance | null;
};

/**
 * Build a `DementiaCareContext` from upstream signals. The result is
 * context-strength-tiered, NOT a diagnosis.
 */
export function buildDementiaCareContext(input: BuildContextInput): DementiaCareContext {
  const documented =
    input.documented_diagnosis_quote !== null &&
    DIAGNOSIS_ACTIVATION_PATTERNS.some((p) => p.test(input.documented_diagnosis_quote!));
  const suspected = input.suspected_impairment_signals.some((s) =>
    SUSPECTED_IMPAIRMENT_PATTERNS.some((p) => p.test(s)),
  );
  const caregiverConcern = input.caregiver_concern_signals.some((s) =>
    CAREGIVER_CONCERN_PATTERNS.some((p) => p.test(s)),
  );
  const activeWorkflow = input.active_workflow_ids.some(isActiveCognitiveCareWorkflow);

  const contextStrength = computeContextStrength({
    documented,
    suspected,
    caregiverConcern,
    activeWorkflow,
  });

  const subtype = input.documented_subtype
    ? normalizeSubtype(input.documented_subtype)
    : null;

  const ctx: DementiaCareContext = DementiaCareContextSchema.parse({
    context_id: input.context_id,
    documented_dementia: documented,
    documented_subtype: subtype,
    caregiver_concern: caregiverConcern,
    suspected_cognitive_impairment: suspected,
    active_cognitive_care_workflow: activeWorkflow,
    context_strength: contextStrength,
    pending_evaluation: input.pending_evaluation,
    diagnosis_quote: documented ? input.documented_diagnosis_quote : null,
    diagnosis_quote_provenance: documented ? input.diagnosis_quote_provenance : null,
  });

  return ctx;
}

function computeContextStrength(input: {
  documented: boolean;
  suspected: boolean;
  caregiverConcern: boolean;
  activeWorkflow: boolean;
}): ContextStrength {
  if (input.documented) return "established";
  if (input.suspected || input.activeWorkflow) return "under_investigation";
  if (input.caregiverConcern) return "concern_only";
  return "none";
}

function normalizeSubtype(raw: string): DementiaCareContext["documented_subtype"] {
  const t = raw.toLowerCase();
  if (/alzheimer/.test(t)) return "alzheimer_disease";
  if (/lewy/.test(t)) return "lewy_body_dementia";
  if (/frontotemporal|ftd/.test(t)) return "frontotemporal_dementia";
  if (/vascular/.test(t)) return "vascular_dementia";
  if (/mixed/.test(t)) return "mixed_dementia";
  if (/unspecified|unknown|nonspecific/.test(t)) return "unspecified_dementia";
  return "other";
}

// ─── Context Strength Semantics ───────────────────────────────────────────

/**
 * The interpretation strength at a given context.
 * This is the irreducible distinction that makes DSI distinct from
 * generic change detection.
 */
export const CONTEXT_STRENGTH_SEMANTICS: Readonly<
  Record<ContextStrength, { pattern_qualifier: string; required_evidence: string }>
> = {
  none: {
    pattern_qualifier: "context-neutral observation only",
    required_evidence: "no dementia-specific interpretation permitted",
  },
  concern_only: {
    pattern_qualifier: "caregiver concern present; pending evaluation",
    required_evidence: "caregiver concern signal",
  },
  under_investigation: {
    pattern_qualifier: "context active; pattern strength downweighted",
    required_evidence: "suspected impairment OR active cognitive-care workflow",
  },
  established: {
    pattern_qualifier: "documented context; full care-relevance framework",
    required_evidence: "documented diagnosis quote in source",
  },
};

/**
 * Whether a context strength permits dementia-care interpretation at all.
 * `none` means: do not interpret; treat observations as generic.
 */
export function isDementiaContextActive(strength: ContextStrength): boolean {
  return strength !== "none";
}

// ─── Source attribution helper (provenance) ────────────────────────────────

export type BuildProvenanceInput = {
  source_type: SourceType;
  observer_id: string | null;
  observed_at: string | null;
  raw_text: string;
  document_id?: string | null;
  source_authored_at?: string | null;
};

export function buildProvenance(input: BuildProvenanceInput): Provenance {
  return {
    source_type: input.source_type,
    observer_id: input.observer_id,
    observed_at: input.observed_at,
    raw_text: input.raw_text,
    captured_at: new Date().toISOString(),
    source_authored_at: input.source_authored_at ?? null,
    document_id: input.document_id ?? null,
  };
}

// Re-export schema for callers who want runtime validation
export { DementiaCareContextSchema };
