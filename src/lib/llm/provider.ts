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
