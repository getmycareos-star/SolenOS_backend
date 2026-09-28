/**
 * SolenOS LLM provider abstraction.
 *
 * Provider-agnostic boundary. Runtime AI goes through here — no module outside
 * this layer should know which model or host backs it.
 */

export interface LlmRequest {
  system: string;
  user: string;
  temperature?: number;
  maxTokens?: number;
  json?: boolean;
  signal?: AbortSignal;
  /**
   * Qwen3-style hidden chain-of-thought pass before the structured extraction pass.
   * When true the model emits a reasoning block first, then the JSON contract.
   * The reasoning block is stripped downstream — it never enters the JSON fields.
   */
  thinking?: boolean;
  /**
   * Fixed RNG seed for deterministic extraction (identical inputs → identical output).
   * Set alongside temperature: 0 to satisfy the determinism contract.
   */
  seed?: number;
  /**
   * Ollama context window size (num_ctx). Bounded to avoid OOM on the 30B model.
   */
  numCtx?: number;
}

export interface LlmResponse {
  content: string;
  model: string;
  provider: string;
  usage?: {
    prompt_tokens?: number;
    completion_tokens?: number;
  };
}

export interface LlmProvider {
  isAvailable(signal?: AbortSignal): Promise<boolean>;
  invoke(request: LlmRequest): Promise<LlmResponse>;
}
