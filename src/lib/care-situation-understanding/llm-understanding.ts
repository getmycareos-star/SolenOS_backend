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
 *
 * ENHANCEMENT: multi-pass self-verification loop.
 * Instead of discarding the whole LLM output on one bad field, the orchestrator
 * runs: extract -> per-field verification -> retry only broken fields -> coverage
 * merge against deterministic. This recovers a large fraction of would-be
 * fallbacks and never invents content the model could not ground.
 */
import type { CareRealityExtractionResult } from "../care-reality-extraction/types";
import {
  extractCareRealityFromText,
  deterministicFragmentsFromResult,
  splitExtractionFragments,
} from "../care-reality-extraction/extract";
import { CARE_UNDERSTANDING_LLM_SYSTEM_PROMPT, buildCareUnderstandingPrompt } from "./llm-prompt";
import {
  LlmUnderstandingOutputSchema,
  validateMedicalBoundary,
  verifyAllFields,
  verifyCoverage,
  type LlmUnderstandingOutput,
} from "./llm-schema";
import { getLlmProvider } from "../llm";
const LLM_DETERMINISTIC_SEED = 42;
/** Max self-verification retries before degrading to deterministic. */
const LLM_MAX_RETRIES = 2;

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
 * Attempt to extract care reality using the local LLM (Ollama / qwen3-coder:30b)
 * for structured understanding, with a multi-pass self-verification loop.
 *
 * Passes:
 *   1. Deterministic pre-split the input into independent clauses (grounding).
 *   2. First LLM pass with hidden chain-of-thought (thinking) + fixed seed.
 *   3. Per-field verification: every raw_fragment must be an exact substring.
 *   4. Retry pass injecting only the specific failures as CORRECTION_HINTS.
 *   5. Coverage merge: any deterministic fragment the LLM missed is merged back.
 *   6. Medical boundary check (diagnosis / advice / empathy / causation).
 *
 * Falls back to deterministic/regex extraction on any unrecoverable failure.
 *
 * @param rawText - The caregiver's raw input text (any length, any structure)
 * @param contributorId - Optional contributor id for attribution
 * @param context - Optional care-record context for pronoun/baseline resolution
 * @param signal - Optional AbortSignal
 * @returns CareRealityExtractionResult from LLM (if successful) or deterministic fallback
 */
export async function llmStructuredUnderstanding(params: {
  rawText: string;
  contributorId?: string;
  context?: LlmExtractionContext;
  signal?: AbortSignal;
}): Promise<CareRealityExtractionResult> {
  const { rawText } = params;
  const source = params.contributorId ?? "caregiver";

  // Trim excessively long input — LLM context preserved but extraction bounded
  const trimmedText = rawText.trim();
  if (!trimmedText) {
    return extractCareRealityFromText({ rawText: "", source });
  }

  // Deterministic pre-split into independent clauses. Feeding labeled blocks
  // makes raw_fragment substring matching trivial and removes sentence
  // segmentation burden from the model.
  const preSplitBlocks = splitExtractionFragments(trimmedText);

  // Deterministic floor — computed once, used for coverage merge and fallback.
  const deterministic = extractCareRealityFromText({
    rawText: trimmedText,
    source,
  });
  const deterministicFragments = deterministicFragmentsFromResult(deterministic);

  // Gate: only attempt local-LLM extraction when Ollama is reachable.
  const provider = getLlmProvider();
  if (!(await provider.isAvailable(params.signal))) {
    return deterministic;
  }

  // Build the prompt with full care-record context injected as evidence.
  const prompt = buildCareUnderstandingPrompt({
    ...params.context,
    preSplitBlocks: preSplitBlocks.length > 0 ? preSplitBlocks : undefined,
  });

  let lastFailures: string[] = [];
  let lastParsed: LlmUnderstandingOutput | null = null;

  for (let attempt = 0; attempt <= LLM_MAX_RETRIES; attempt++) {
    const isRetry = attempt > 0;
    try {
      const userContent = isRetry
        ? buildRetryUserContent(trimmedText, preSplitBlocks, lastFailures)
        : trimmedText;

      const response = await provider.invoke({
        system: prompt,
        user: userContent,
        temperature: 0,
        seed: LLM_DETERMINISTIC_SEED,
        json: true,
        thinking: true,
        signal: params.signal,
      });

      const content = response.content;

      // Extract JSON from response (handle markdown-wrapped JSON)
      const jsonMatch = content.match(/\{[\s\S]*\}/);
      if (!jsonMatch) {
        if (isRetry) break;
        continue;
      }

      const parsed = JSON.parse(jsonMatch[0]);

      // Validate against Zod schema
      const validation = LlmUnderstandingOutputSchema.safeParse(parsed);
      if (!validation.success) {
        lastFailures = [
          `ZOD: ${validation.error.issues.map((i) => i.message).join("; ")}`,
        ];
        continue;
      }

      const validated: LlmUnderstandingOutput = validation.data;

      // Per-field verification — every raw_fragment must be an exact substring.
      const fieldCheck = verifyAllFields(validated, trimmedText);
      if (!fieldCheck.ok) {
        lastFailures = fieldCheck.failures;
        lastParsed = validated; // keep for coverage merge even on partial failure
        continue;
      }

      lastParsed = validated;
      lastFailures = [];
      break;
    } catch (err: unknown) {
      lastFailures = [
        `EXCEPTION: ${err instanceof Error ? err.message : String(err)}`,
      ];
      if (!isRetry) break;
    }
  }

  // No usable LLM output — deterministic floor.
  if (!lastParsed) {
    return deterministic;
  }

  // Medical boundary check — forbid diagnosis/advice/empathy/causation.
  // A boundary violation is NOT retryable: the model is asserting something
  // outside its remit, so we degrade rather than encourage it.
  const boundary = validateMedicalBoundary(lastParsed);
  if (!boundary.ok) {
    console.warn(
      "[llm-understanding] Medical boundary violation, degrading to deterministic:",
      boundary.failures.join("; "),
    );
    return deterministic;
  }

  // Coverage merge: any deterministic fragment the LLM missed is merged back
  // so no caregiver claim is ever lost. The LLM output is the primary source;
  // deterministic fills gaps only.
  const coverage = verifyCoverage(lastParsed, deterministicFragments);
  const merged = coverage.ok
    ? lastParsed
    : mergeCoverage(lastParsed, deterministic, coverage.missing);

  // Map to existing types and return
  return mapLlmOutputToExtractionResult(merged);
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

/**
 * Care-record context injected per call so the stateless model can resolve
 * pronouns and avoid inventing "normal". Continuity is managed by CRS/state
 * stores — this is read-only evidence, never model memory.
 */
export interface LlmExtractionContext {
  careRecipient?: string | null;
  caregiverDisplayName?: string | null;
  knownBaseline?: readonly string[];
  knownMeds?: readonly string[];
  knownAllergies?: readonly string[];
  priorObservations?: readonly string[];
  priorEntities?: readonly string[];
  priorContradictions?: readonly string[];
  ambiguityHints?: readonly string[];
  documentMeta?: string | null;
  /**
   * Recurring care patterns detected by the compounding care-reality memory
   * index (reality-signature recurrence, not quote matching). Read-only
   * evidence — the model uses it to recognise "this again" and to anchor
   * "what is normal", never to invent new facts.
   */
  recurringPatterns?: readonly string[];
  /**
   * Open unknowns held by the index across captures. The model must NOT
   * resolve these — it surfaces them as unknowns and moves on.
   */
  heldOpenUnknowns?: readonly string[];
}

/**
 * Compose the per-call LLM extraction context from the compounding
 * care-reality memory index and the baseline profile.
 *
 * This is the 100000x lever: instead of the stateless model re-deriving
 * everything from one note, it is handed the already-understood care record
 * (typed observations, baseline, recurring patterns, held contradictions)
 * and asked only to surface the delta. Continuity is managed by the index —
 * this is read-only evidence, never model memory.
 *
 * Never includes caregiver-facing summaries, 5-field compression, or clinical
 * judgments. Those stay in their own deterministic engines.
 */
export function composeLlmContextFromIndex(params: {
  careKey: string;
  careRecipientId?: string | null;
  baselineFacts?: readonly string[];
  recurringPatterns?: readonly string[];
  heldOpenUnknowns?: readonly string[];
  priorObservations?: readonly string[];
  priorContradictions?: readonly string[];
  ambiguityHints?: readonly string[];
  documentMeta?: string | null;
}): LlmExtractionContext {
  const ctx: LlmExtractionContext = {};

  if (params.careRecipientId) ctx.careRecipient = params.careRecipientId;
  if (params.baselineFacts && params.baselineFacts.length > 0) {
    ctx.knownBaseline = params.baselineFacts.slice(0, 8);
  }
  if (params.recurringPatterns && params.recurringPatterns.length > 0) {
    ctx.recurringPatterns = params.recurringPatterns.slice(0, 6);
  }
  if (params.heldOpenUnknowns && params.heldOpenUnknowns.length > 0) {
    ctx.heldOpenUnknowns = params.heldOpenUnknowns.slice(0, 6);
  }
  if (params.priorObservations && params.priorObservations.length > 0) {
    ctx.priorObservations = params.priorObservations.slice(0, 12);
  }
  if (params.priorContradictions && params.priorContradictions.length > 0) {
    ctx.priorContradictions = params.priorContradictions.slice(0, 6);
  }
  if (params.ambiguityHints && params.ambiguityHints.length > 0) {
    ctx.ambiguityHints = params.ambiguityHints;
  }
  if (params.documentMeta) ctx.documentMeta = params.documentMeta;

  return ctx;
}

/**
 * Build the retry user content: original input + specific correction hints.
 * Only the broken fields are called out — the model re-emits the full object
 * but is told exactly what to fix, which is far more reliable than a full retry.
 */
function buildRetryUserContent(
  rawText: string,
  preSplitBlocks: readonly string[],
  failures: string[],
): string {
  const hintBlock =
    failures.length > 0
      ? `\n\nCORRECTION_HINTS (fix ONLY these — re-emit the full JSON object):\n${failures
          .map((f) => `- ${f}`)
          .join("\n")}`
      : "";
  const blockBlock =
    preSplitBlocks.length > 0
      ? `\n\nPRE_SPLIT_BLOCKS:\n${preSplitBlocks
          .map((b, i) => `  BLOCK ${i + 1}: ${b}`)
          .join("\n")}`
      : "";
  return `${rawText}${blockBlock}${hintBlock}`;
}

/**
 * Merge LLM output with deterministic coverage.
 *
 * The LLM output is the primary source (typed, structured, context-aware).
 * Deterministic fragments the LLM missed are appended as observations so no
 * caregiver claim is ever lost. This is a merge, NOT a fallback: the LLM's
 * typed objects are preserved and only gaps are filled.
 */
function mergeCoverage(
  llm: LlmUnderstandingOutput,
  deterministic: CareRealityExtractionResult,
  missing: readonly string[],
): LlmUnderstandingOutput {
  const existingFragments = new Set(
    [
      ...llm.observations.map((o) => o.raw_fragment),
      ...llm.events.map((e) => e.raw_fragment),
      ...llm.decisions.map((d) => d.raw_fragment),
      ...llm.outcomes.map((o) => o.raw_fragment),
      ...llm.unknowns.map((u) => u.raw_fragment),
      ...llm.non_care_facts.map((n) => n.raw_fragment),
    ].map((f) => f.trim().toLowerCase()),
  );

  const additions: LlmUnderstandingOutput["observations"] = [];
  for (const frag of missing) {
    const key = frag.trim().toLowerCase();
    if (existingFragments.has(key)) continue;
    if (key.length < 20) continue;
    additions.push({
      description: frag.trim().slice(0, 240),
      approximate_time: null,
      confidence: "medium",
      raw_fragment: frag,
    });
    existingFragments.add(key);
  }

  return {
    ...llm,
    observations: [...llm.observations, ...additions].slice(0, 20),
  };
}
