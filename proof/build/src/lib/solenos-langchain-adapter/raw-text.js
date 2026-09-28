"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.extractRawLLMText = extractRawLLMText;
/**
 * Extract plain text from LangChain message content — no parsing or validation.
 *
 * When Qwen3 hidden chain-of-thought is enabled, Ollama may emit the reasoning
 * block either as a separate `thinking` field on the message or inline inside
 * `content` wrapped in <thinking>...</thinking> tags. Both forms are stripped
 * here so the reasoning block can never reach the typed JSON contract.
 */
function extractRawLLMText(content) {
    if (typeof content === "string") {
        return stripThinkingBlock(content);
    }
    if (Array.isArray(content)) {
        return stripThinkingBlock(content
            .map((part) => {
            if (typeof part === "string")
                return part;
            if (part && typeof part === "object" && "text" in part) {
                return String(part.text);
            }
            return "";
        })
            .join(""));
    }
    if (content == null) {
        return "";
    }
    return stripThinkingBlock(String(content));
}
/** Remove any inline <thinking>...</thinking> reasoning block from model output. */
function stripThinkingBlock(text) {
    if (!text)
        return "";
    return text
        .replace(/<thinking>[\s\S]*?<\/thinking>/gi, "")
        .replace(/^\s*```(?:json)?\s*([\s\S]*?)\s*```\s*$/s, "$1")
        .trim();
}
