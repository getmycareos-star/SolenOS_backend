/**
 * LLM Understanding Orchestrator.
 *
 * Tries structured LLM extraction first (Ollama / qwen3-coder:30b, local) with
 * Zod validation + medical boundary check. Falls back to deterministic/regex
 * extraction (existing path) when:
 * - Local LLM (Ollama) is not available
 * - LLM call fails
 * - LLM output fails Zod validation
 * - LLM output violates medical boundary (diagnosis/advice/empathy/causation)
 *
 * Never loses caregiver input. Never feeds /api/analyze 5-field compression to caregiver panel.
 */
import type { CareRealityExtractionResult } from "../care-reality-extraction/types";
import { extractCareRealityFromText } from "../care-reality-extraction/extract";
import { CARE_UNDERSTANDING_LLM_SYSTEM_PROMPT } from "./llm-prompt";
import {
  LlmUnderstandingOutputSchema,
  validateMedicalBoundary,
  type LlmUnderstandingOutput,
} from "./llm-schema";
import { getLlmProvider } from "../llm";

/**
 * Map LLM typed output to existing CareRealityExtractionResult types.
 * Assigns stable IDs and maintains structure compatibility.
 */
function mapLlmOutputToExtractionResult(
  llm: LlmUnderstandingOutput,
): CareRealityExtractionResult {
  const observations = llm.observations.map((o, i) => ({
    id: `llm_obs_${Date.now().toString(36)}_${i}_${Math.random().toString(36).slice(2, 7)}`,
    layer: "observation" as const,
    description: o.description,
    approximate_time: o.approximate_time,
    source: "caregiver",
    confidence: o.confidence,
    raw_fragment: o.raw_fragment,
  }));

  const events = llm.events.map((e, i) => ({
    id: `llm_evt_${Date.now().toString(36)}_${i}_${Math.random().toString(36).slice(2, 7)}`,
    layer: "event" as const,
    description: e.description,
    time: e.time,
    participants: e.participants,
    related_observation_ids: [] as string[],
    raw_fragment: e.raw_fragment,
  }));

  const decisions = llm.decisions.map((d, i) => ({
    id: `llm_dec_${Date.now().toString(36)}_${i}_${Math.random().toString(36).slice(2, 7)}`,
    layer: "decision" as const,
    description: d.description,
    who: d.who,
    why: d.why,
    reason_unknown: d.reason_unknown,
    evidence_texts: [] as string[],
    alternatives: [] as string[],
    outcome: null as string | null,
    status: d.status,
    raw_fragment: d.raw_fragment,
  }));

  const outcomes = llm.outcomes.map((o, i) => ({
    id: `llm_out_${Date.now().toString(36)}_${i}_${Math.random().toString(36).slice(2, 7)}`,
    layer: "outcome" as const,
    description: o.description,
    related_id: null as string | null,
    related_type: null as "decision" | "event" | null,
    time: null as string | null,
    evidence_texts: [] as string[],
    status: o.status,
    raw_fragment: o.raw_fragment,
  }));

  const unknowns = llm.unknowns.map((u, i) => ({
    id: `llm_unk_${Date.now().toString(36)}_${i}_${Math.random().toString(36).slice(2, 7)}`,
    layer: "unknown" as const,
    question: u.question,
    related_object_id: null as string | null,
    related_object_type: null as "observation" | "event" | "decision" | null,
    source: "caregiver",
    importance: "Identified by structured understanding layer — needs confirmation.",
    status: u.status,
    raw_fragment: u.raw_fragment,
  }));

  const non_care_facts = llm.non_care_facts.map((n, i) => ({
    id: `llm_ncf_${Date.now().toString(36)}_${i}_${Math.random().toString(36).slice(2, 7)}`,
    layer: n.layer as "contributor_load" | "disagreement_perspective",
    text: n.text,
    raw_fragment: n.raw_fragment,
  }));

  const relationships = llm.possible_links.map((l, i) => ({
    id: `llm_rel_${Date.now().toString(36)}_${i}_${Math.random().toString(36).slice(2, 7)}`,
    from_id: "",
    to_id: "",
    kind: "observation_to_observation" as const,
    certainty: "possible" as const,
    evidence_note: l.text,
  }));

  return {
    observations,
    events,
    decisions,
    actions: [],
    outcomes,
    unknowns,
    non_care_facts,
    relationships,
    observation_focus_lines: observations.map((o) =>
      o.description.endsWith(".") ? o.description : `${o.description}.`,
    ),
  };
}

/**
 * Attempt to extract care reality using the local LLM (Ollama / qwen3-coder:30b) for structured understanding.
 * Falls back to deterministic/regex extraction on any failure.
 *
 * @param rawText - The caregiver's raw input text (any length, any structure)
 * @param contributorId - Optional contributor id for attribution
 * @returns CareRealityExtractionResult from LLM (if successful) or deterministic fallback
 */
export async function llmStructuredUnderstanding(params: {
  rawText: string;
  contributorId?: string;
  signal?: AbortSignal;
}): Promise<CareRealityExtractionResult> {
  const { rawText } = params;
  const source = params.contributorId ?? "caregiver";

  // Trim excessively long input — LLM context preserved but extraction bounded
  const trimmedText = rawText.trim();
  if (!trimmedText) {
    return extractCareRealityFromText({ rawText: "", source });
  }

  // Gate: only attempt local-LLM extraction when Ollama is reachable.
  // If it is not available, fall back to deterministic extraction (no external
  // dependency, no blocked input — raw caregiver text is always preserved).
  const provider = getLlmProvider();
  if (!(await provider.isAvailable(params.signal))) {
    return extractCareRealityFromText({ rawText: trimmedText, source });
  }

  try {
    const response = await provider.invoke({
      system: CARE_UNDERSTANDING_LLM_SYSTEM_PROMPT,
      user: trimmedText,
      temperature: 0,
      json: true,
      signal: params.signal,
    });

    const content = response.content;

    // Extract JSON from response (handle markdown-wrapped JSON)
    const jsonMatch = content.match(/\{[\s\S]*\}/);
    if (!jsonMatch) {
      return extractCareRealityFromText({ rawText: trimmedText, source });
    }

    const parsed = JSON.parse(jsonMatch[0]);

    // Validate against Zod schema
    const validation = LlmUnderstandingOutputSchema.safeParse(parsed);
    if (!validation.success) {
      console.warn(
        "[llm-understanding] Zod validation failed:",
        validation.error.issues.map((i) => i.message).join("; "),
      );
      return extractCareRealityFromText({ rawText: trimmedText, source });
    }

    const validated: LlmUnderstandingOutput = validation.data;

    // Medical boundary check — forbid diagnosis/advice/empathy/causation
    const boundary = validateMedicalBoundary(validated);
    if (!boundary.ok) {
      console.warn(
        "[llm-understanding] Medical boundary violation:",
        boundary.failures.join("; "),
      );
      return extractCareRealityFromText({ rawText: trimmedText, source });
    }

    // Map to existing types and return
    return mapLlmOutputToExtractionResult(validated);
  } catch (err: unknown) {
    console.warn(
      "[llm-understanding] LLM extraction failed, falling back to deterministic:",
      err instanceof Error ? err.message : String(err),
    );
    return extractCareRealityFromText({ rawText: trimmedText, source });
  }
}

/**
 * Synchronous deterministic-only understanding path.
 * Always uses regex/heuristic extraction — no LLM dependency.
 * Use when you need guaranteed synchronous execution.
 */
export function deterministicUnderstanding(params: {
  rawText: string;
  contributorId?: string;
}): CareRealityExtractionResult {
  return extractCareRealityFromText({
    rawText: params.rawText,
    source: params.contributorId ?? "caregiver",
  });
}
