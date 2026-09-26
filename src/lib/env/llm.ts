/**
 * SolenOS LLM environment configuration (local Ollama).
 *
 * No external API key is required for local AI. The model host is Ollama.
 */
import { LLM_PROVIDER_OLLAMA, DEFAULT_LLM_MODEL, DEFAULT_OLLAMA_BASE_URL, getLlmModel, getLlmBaseUrl } from "../llm/env";

export { LLM_PROVIDER_OLLAMA, DEFAULT_LLM_MODEL, DEFAULT_OLLAMA_BASE_URL };
export { getLlmModel, getLlmBaseUrl };

export const LLM_ENV_MISSING_MESSAGE =
  "Local LLM (Ollama / qwen3-coder:30b) is not available. Run `ollama serve` and `ollama pull qwen3-coder:30b`.";

/**
 * Local-LLM availability is determined by Ollama reachability at runtime
 * (see OllamaProvider.isAvailable). There is no API key to configure.
 */
export function getLlmAvailabilityStatus(): {
  available: boolean;
  model: string;
  baseUrl: string;
} {
  return {
    available: true,
    model: getLlmModel(),
    baseUrl: getLlmBaseUrl(),
  };
}
