import { LLM_OUTPUT_SCHEMA_JSON } from "../final-output-contract";

import { SOLENOS_SYSTEM_PROMPT } from "../solenos-langchain-adapter/system-prompt";

import type { ContextWindowOutput } from "../context-window-strategy";
import type { DocumentIntakeOutput } from "../document-intake";
import type { GroundingContextPackage } from "../telemetry-persistence/schema";
import {
  type BehaviorProfile,
  formatBehaviorConstraint,
} from "../input-classification";
import type { UrgencyDetectionResult } from "../urgency-detection";

import { stableStringifyContextPayload } from "./serialize";

export const LLM_MVP_MODEL = "qwen3-coder:30b";

export const LLM_OUTPUT_SCHEMA = LLM_OUTPUT_SCHEMA_JSON;

export const LLM_INITIAL_RULE =
  "Return ONLY valid JSON. No markdown. No explanations. No extra text.";

export const LLM_RETRY_RULE =
  "Return ONLY valid JSON matching the schema exactly. No extra text.";

export interface LlmEnvelopeOptions {
  behaviorProfile?: BehaviorProfile | null;
  urgencyDetection?: UrgencyDetectionResult | null;
  safetyOverrideLine?: string | null;
  /** Observational tags only — no routing, schema, or lifecycle effects. */
  observationTags?: readonly string[] | null;
}

export function buildLlmExecutionEnvelope(
  contextWindow: ContextWindowOutput,
  retry: boolean,
  documentIntake?: DocumentIntakeOutput | null,
  options?: LlmEnvelopeOptions | null,
  groundingContext?: GroundingContextPackage | null,
): { system: string; user: string } {
  const rule = retry ? LLM_RETRY_RULE : LLM_INITIAL_RULE;
  const behaviorLine = options?.behaviorProfile
    ? `BEHAVIOR_CONSTRAINT: ${formatBehaviorConstraint(options.behaviorProfile)}`
    : null;
  const urgencyLine = options?.urgencyDetection
    ? `DETECTED_URGENCY: ${options.urgencyDetection.risk_level}`
    : null;
  const safetyLine = options?.safetyOverrideLine ?? null;
  const observationLines = options?.observationTags ?? [];

  return {
    system: SOLENOS_SYSTEM_PROMPT,
    user: [
      `RULE: ${rule}`,
      `SCHEMA: ${LLM_OUTPUT_SCHEMA}`,
      ...(urgencyLine ? [urgencyLine] : []),
      ...(behaviorLine ? [behaviorLine] : []),
      ...(safetyLine ? [safetyLine] : []),
      ...observationLines,
      `INPUT: ${stableStringifyContextPayload(contextWindow, documentIntake, groundingContext)}`,
    ].join("\n"),
  };
}
