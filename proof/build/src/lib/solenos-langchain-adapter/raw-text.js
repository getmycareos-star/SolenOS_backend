"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.extractRawLLMText = extractRawLLMText;
/** Extract plain text from LangChain message content — no parsing or validation. */
function extractRawLLMText(content) {
    if (typeof content === "string") {
        return content;
    }
    if (Array.isArray(content)) {
        return content
            .map((part) => {
            if (typeof part === "string")
                return part;
            if (part && typeof part === "object" && "text" in part) {
                return String(part.text);
            }
            return "";
        })
            .join("");
    }
    if (content == null) {
        return "";
    }
    return String(content);
}
