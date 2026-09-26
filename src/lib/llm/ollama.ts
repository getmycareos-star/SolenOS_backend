/**
 * Ollama provider — talks directly to the local Ollama REST API.
 *
 * Uses Ollama's OpenAI-compatible `/api/chat` endpoint (no external deps).
 * Enforces JSON output via the `format` field so structured extraction is
 * guaranteed JSON, then validated downstream with Zod.
 */
import { DEFAULT_OLLAMA_BASE_URL, getLlmBaseUrl, getLlmModel, LLM_PROVIDER_OLLAMA } from "./env";
import type { LlmProvider, LlmRequest, LlmResponse } from "./provider";
import { extractRawLLMText } from "../solenos-langchain-adapter/raw-text";

function resolveBaseUrl(): string {
  let base = getLlmBaseUrl();
  if (base.endsWith("/")) base = base.slice(0, -1);
  return base;
}

interface OllamaChatMessage {
  role: "system" | "user" | "assistant";
  content: string;
}

interface OllamaChatResponse {
  model: string;
  done: boolean;
  message?: {
    role: string;
    content: string;
  };
  prompt_eval_count?: number;
  eval_count?: number;
  eval_duration?: number;
}

export class OllamaProvider implements LlmProvider {
  readonly provider = LLM_PROVIDER_OLLAMA;

  async isAvailable(signal?: AbortSignal): Promise<boolean> {
    try {
      const ctrl = new AbortController();
      const timeout = setTimeout(() => ctrl.abort(), 2000);
      const sig = signal ?? ctrl.signal;
      const res = await fetch(`${resolveBaseUrl()}/api/tags`, {
        method: "GET",
        signal: sig,
      });
      clearTimeout(timeout);
      return res.ok;
    } catch {
      return false;
    }
  }

  async invoke(request: LlmRequest): Promise<LlmResponse> {
    const base = resolveBaseUrl();
    const model = getLlmModel();

    const messages: OllamaChatMessage[] = [
      { role: "system", content: request.system },
      { role: "user", content: request.user },
    ];

    const body: Record<string, unknown> = {
      model,
      messages,
      stream: false,
      options: {
        temperature: request.temperature ?? 0,
        ...(request.maxTokens ? { num_predict: request.maxTokens } : {}),
      },
    };

    // Qwen3 supports Ollama `format` JSON mode for structured output.
    if (request.json) {
      (body.options as Record<string, unknown>).format = "json";
    }

    const res = await fetch(`${base}/api/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: request.signal,
    });

    if (!res.ok) {
      throw new Error(
        `Ollama request failed: ${res.status} ${res.statusText}`,
      );
    }

    const data = (await res.json()) as OllamaChatResponse;
    const content = extractRawLLMText(data.message?.content ?? "");

    return {
      content,
      model: data.model ?? model,
      provider: this.provider,
      usage: {
        prompt_tokens: data.prompt_eval_count,
        completion_tokens: data.eval_count,
      },
    };
  }
}
