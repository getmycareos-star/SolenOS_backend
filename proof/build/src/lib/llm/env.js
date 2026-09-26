"use strict";
/**
 * Local LLM configuration (Ollama).
 *
 * Required runtime values live under the OLLAMA_* namespace. There is NO
 * external API-key requirement for local AI functionality.
 */
Object.defineProperty(exports, "__esModule", { value: true });
exports.DEFAULT_OLLAMA_BASE_URL = exports.DEFAULT_LLM_MODEL = exports.LLM_PROVIDER_OLLAMA = void 0;
exports.getLlmModel = getLlmModel;
exports.getLlmBaseUrl = getLlmBaseUrl;
exports.llmProviderName = llmProviderName;
exports.isLlmProviderOllama = isLlmProviderOllama;
exports.LLM_PROVIDER_OLLAMA = "ollama";
exports.DEFAULT_LLM_MODEL = "qwen3-coder:30b";
exports.DEFAULT_OLLAMA_BASE_URL = "http://127.0.0.1:11434";
/**
 * Model resolution order: OLLAMA_MODEL env > DEFAULT_LLM_MODEL.
 * The local model is always qwen3-coder:30b unless explicitly overridden.
 */
function getLlmModel() {
    return (process.env.OLLAMA_MODEL?.trim() || exports.DEFAULT_LLM_MODEL).trim();
}
function getLlmBaseUrl() {
    return (process.env.OLLAMA_BASE_URL?.trim() || exports.DEFAULT_OLLAMA_BASE_URL).trim();
}
function llmProviderName() {
    return process.env.LLM_PROVIDER?.trim() || exports.LLM_PROVIDER_OLLAMA;
}
/** True only when the operator has explicitly enabled a non-default LLM provider. */
function isLlmProviderOllama() {
    return llmProviderName() === exports.LLM_PROVIDER_OLLAMA;
}
