/**
 * Local LLM configuration (Ollama).
 *
 * Required runtime values live under the OLLAMA_* namespace. There is NO
 * external API-key requirement for local AI functionality.
 */

export const LLM_PROVIDER_OLLAMA = "ollama" as const;

export const DEFAULT_LLM_MODEL = "qwen3-coder:30b";

export const DEFAULT_OLLAMA_BASE_URL = "http://127.0.0.1:11434";

/**
 * Model resolution order: OLLAMA_MODEL env > DEFAULT_LLM_MODEL.
 * The local model is always qwen3-coder:30b unless explicitly overridden.
 */
export function getLlmModel(): string {
  return (process.env.OLLAMA_MODEL?.trim() || DEFAULT_LLM_MODEL).trim();
}

export function getLlmBaseUrl(): string {
  return (process.env.OLLAMA_BASE_URL?.trim() || DEFAULT_OLLAMA_BASE_URL).trim();
}

export function llmProviderName(): string {
  return process.env.LLM_PROVIDER?.trim() || LLM_PROVIDER_OLLAMA;
}

/** True only when the operator has explicitly enabled a non-default LLM provider. */
export function isLlmProviderOllama(): boolean {
  return llmProviderName() === LLM_PROVIDER_OLLAMA;
}
