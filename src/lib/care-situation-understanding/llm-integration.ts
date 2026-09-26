/**
 * LLM integration bridge for the live /api/situation ingestion path.
 *
 * Invokes the local-LLM structured extractor (Ollama / qwen3-coder:30b) over the
 * raw caregiver input and projects the resulting typed claims onto the
 * caregiver-facing understanding fields (what_i_understood / what_is_uncertain).
 *
 * Never blocks ingestion: any LLM failure (unreachable, timeout, validation,
 * boundary) degrades to an empty projection so the deterministic path is
 * unchanged. The raw caregiver input is always preserved upstream regardless.
 */
import { llmStructuredUnderstanding } from "./llm-understanding";
import type { CareRealityExtractionResult } from "../care-reality-extraction/types";

export interface LlmUnderstandingProjection {
  observationLabels: string[];
  unknownQuestions: string[];
  disagreementFacts: string[];
  decisionDescriptions: string[];
  outcomeDescriptions: string[];
  events: Array<{ description: string; time: string | null }>;
}

export async function projectLlmUnderstanding(params: {
  rawText: string;
  contributorId?: string;
  timeoutMs?: number;
}): Promise<LlmUnderstandingProjection> {
  const empty: LlmUnderstandingProjection = {
    observationLabels: [],
    unknownQuestions: [],
    disagreementFacts: [],
    decisionDescriptions: [],
    outcomeDescriptions: [],
    events: [],
  };

  const trimmed = (params.rawText ?? "").trim();
  if (!trimmed) return empty;

  try {
    const signal = AbortSignal.timeout(params.timeoutMs ?? 5000);
    const extraction: CareRealityExtractionResult = await llmStructuredUnderstanding({
      rawText: trimmed,
      contributorId: params.contributorId ?? "caregiver",
      signal,
    });

    return {
      observationLabels: extraction.observations.map((o) => o.description),
      unknownQuestions: extraction.unknowns.map((u) => u.question),
      disagreementFacts: extraction.non_care_facts
        .filter((n) => n.layer === "disagreement_perspective")
        .map((n) => n.text),
      decisionDescriptions: extraction.decisions.map((d) => d.description),
      outcomeDescriptions: extraction.outcomes.map((o) => o.description),
      events: extraction.events.map((e) => ({
        description: e.description,
        time: e.time ?? null,
      })),
    };
  } catch (err: unknown) {
    // Degradation is silent and safe: the deterministic path keeps running.
    if (process.env.NODE_ENV !== "production") {
      const msg = err instanceof Error ? err.message : String(err);
      if (msg.includes("aborted") || msg.includes("timeout")) {
        console.warn("[llm-integration] LLM extraction timed out; using deterministic path");
      } else {
        console.warn(
          "[llm-integration] LLM extraction unavailable; using deterministic path:",
          msg,
        );
      }
    }
    return empty;
  }
}
