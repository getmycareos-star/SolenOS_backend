"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.OllamaProvider = void 0;
/**
 * Ollama provider — talks directly to the local Ollama REST API.
 *
 * Uses Ollama's OpenAI-compatible `/api/chat` endpoint (no external deps).
 * Enforces JSON output via the `format` field so structured extraction is
 * guaranteed JSON, then validated downstream with Zod.
 */
const env_1 = require("./env");
const raw_text_1 = require("../solenos-langchain-adapter/raw-text");
function resolveBaseUrl() {
    let base = (0, env_1.getLlmBaseUrl)();
    if (base.endsWith("/"))
        base = base.slice(0, -1);
    return base;
}
class OllamaProvider {
    provider = env_1.LLM_PROVIDER_OLLAMA;
    async isAvailable(signal) {
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
        }
        catch {
            return false;
        }
    }
    async invoke(request) {
        const base = resolveBaseUrl();
        const model = (0, env_1.getLlmModel)();
        const messages = [
            { role: "system", content: request.system },
            { role: "user", content: request.user },
        ];
        const body = {
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
            body.options.format = "json";
        }
        const res = await fetch(`${base}/api/chat`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
            signal: request.signal,
        });
        if (!res.ok) {
            throw new Error(`Ollama request failed: ${res.status} ${res.statusText}`);
        }
        const data = (await res.json());
        const content = (0, raw_text_1.extractRawLLMText)(data.message?.content ?? "");
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
exports.OllamaProvider = OllamaProvider;
