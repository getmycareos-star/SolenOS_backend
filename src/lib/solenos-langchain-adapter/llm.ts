import { HumanMessage, SystemMessage } from "@langchain/core/messages";
import {
  buildLlmExecutionEnvelope,
  type LlmEnvelopeOptions,
} from "../llm-contract";
import { getLlmProvider } from "../llm";
import { DEFAULT_SOLENOS_LANGUAGE, makeLanguageAwarePrompt } from "../multilingual-execution";
import type { SolenOSLanguage } from "../multilingual-execution";
import type { ContextWindowOutput } from "../context-window-strategy";
import type { DocumentIntakeOutput } from "../document-intake";
import type { GroundingContextPackage } from "../telemetry-persistence/schema";

export interface LlmExecutionParams {
  contextWindow: ContextWindowOutput;
  documentIntake?: DocumentIntakeOutput | null;
  groundingContext?: GroundingContextPackage | null;
  envelopeOptions?: LlmEnvelopeOptions | null;
  model?: string;
  retry?: boolean;
  userLanguage?: SolenOSLanguage;
}

/**
 * Single-pass local-LLM (Ollama / qwen3-coder:30b) invocation with the required
 * execution envelope. No API key — the model is local.
 */
export async function invokeLlmExecution(params: LlmExecutionParams): Promise<string> {
  const envelope = buildLlmExecutionEnvelope(
    params.contextWindow,
    params.retry ?? false,
    params.documentIntake,
    params.envelopeOptions,
    params.groundingContext,
  );

  const userLanguage = params.userLanguage ?? DEFAULT_SOLENOS_LANGUAGE;
  const wrappedUserPrompt = makeLanguageAwarePrompt(envelope.user, userLanguage);

  const response = await getLlmProvider().invoke({
    system: envelope.system,
    user: wrappedUserPrompt,
    temperature: 0,
    json: true,
  });

  return response.content;
}
