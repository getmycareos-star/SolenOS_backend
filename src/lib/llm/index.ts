import { OllamaProvider } from "./ollama";
import type { LlmProvider, LlmRequest, LlmResponse } from "./provider";

export type { LlmProvider, LlmRequest, LlmResponse } from "./provider";
export { OllamaProvider } from "./ollama";
export * from "./env";

let _provider: LlmProvider | null = null;

export function getLlmProvider(): LlmProvider {
  if (!_provider) {
    _provider = new OllamaProvider();
  }
  return _provider;
}

export function resetLlmProvider(): void {
  _provider = null;
}

export async function invokeLlm(request: LlmRequest): Promise<LlmResponse> {
  return getLlmProvider().invoke(request);
}
