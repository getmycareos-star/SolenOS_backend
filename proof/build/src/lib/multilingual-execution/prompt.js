"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.makeLanguageAwarePrompt = makeLanguageAwarePrompt;
const constants_1 = require("./constants");
/**
 * Wraps every LLM task prompt with deterministic SolenOS multilingual execution rules.
 */
function makeLanguageAwarePrompt(originalPrompt, userLanguage) {
    const langName = constants_1.SOLENOS_LANGUAGE_NAMES[userLanguage] ?? constants_1.SOLENOS_LANGUAGE_NAMES.en;
    return `
SYSTEM ROLE: SolenOS Multilingual Execution Engine
RULES:
- Input may contain English documents.
- Reason in English internally.
- Do NOT translate technical/legal/medical terms:
  (Medi-Cal, Medicare, hospital, doctor, insurance, benefit program names)
- Output MUST be in: ${langName}
- Maintain structure and meaning across languages.
- Only translate OUTPUT layer.
TASK:
${originalPrompt}
OUTPUT LANGUAGE:
${langName}
`.trim();
}
