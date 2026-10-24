# Companion greeting and plain-language behavior

SolenOS Companion must use the configured server-side Qwen model to generate greeting and social-turn replies. Do not replace this behavior with a fixed frontend or backend greeting string.

## Required behavior

- Respond naturally and briefly to greetings and social pleasantries, following the user's wording, tone, and language.
- If the user simply says "hi", greet them back and leave room for them to choose what to discuss. Do not automatically ask "How are you doing?".
- If asked how the assistant is doing, answer honestly as an AI assistant; do not claim human feelings or a personal life.
- Use plain, understandable language. Never emit gibberish, unexplained foreign-language words, internal provider/database/pipeline jargon, or "Saba".
- Do not repeat greetings, invent care facts, or claim that a greeting was saved to the Living Care Record.
- Pure greeting/social-only input from the workspace goes to the Companion endpoint and is not submitted as care evidence. Care facts included with a greeting remain normal care capture input.
- If Qwen is unavailable, return an honest unavailable error and preserve the user's input. Do not substitute a hardcoded greeting.

## Verification

The backend test `test_companion_greeting_uses_model_with_natural_language_contract` verifies that the inference prompt includes the behavior contract and that the endpoint persists the model-generated turn. Frontend tests verify greetings are separated from real care updates.
