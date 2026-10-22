# Qwen provider configuration

SolenOS Companion uses a server-side OpenAI-compatible chat-completions interface for Qwen. The browser never receives the provider API key. Qwen is called only after the backend authorizes the account's access to the selected care record and retrieves record context scoped to that person.

Configure these variables in the backend runtime (Railway), never in Vite/Netlify frontend variables and never in committed source files:

- `QWEN_BASE_URL`: provider API base URL, for example the base URL for an OpenAI-compatible Qwen endpoint. Do not include credentials in the URL.
- `QWEN_MODEL`: the exact model identifier supported by that endpoint.
- `QWEN_API_KEY`: provider credential.
- `QWEN_TIMEOUT_SECONDS`: optional request timeout, default `20` (bounded by the application to 3–60 seconds).

The provider URL may be a managed Qwen endpoint or a separately hosted Qwen inference server that implements `POST {QWEN_BASE_URL}/chat/completions`. A locally installed checkpoint is not reachable from Railway automatically; it must be served at a network endpoint reachable by the backend. Do not point Railway at `localhost` unless Qwen is actually running inside the same deployment container.

## Runtime behavior

- If any required Qwen setting is missing, the API returns `provider_status=not_configured` and explicitly says no AI answer was generated.
- If the provider times out, is unreachable, or returns invalid output, the API returns `provider_status=error`; it does not substitute an invented answer.
- Potential emergency language bypasses Qwen and returns the emergency override.
- The model receives only the authorized care recipient's recent events, linked evidence, active insights, appointments, and the current conversation history.
- Model-returned evidence IDs are filtered against the IDs actually supplied in the authorized context.
- Companion messages are persisted as conversation history only. They do not write to the Living Care Record.
- Qwen output is not a diagnosis, medication instruction, or substitute for clinician judgment.

## Release verification

After setting variables, verify a signed-in test account through `POST /api/v1/conversation/message`. Confirm `provider_status=connected`, `ai_generated=true`, valid JSON output, and citations restricted to evidence IDs returned by the authorized context. Then test missing/invalid credentials, timeouts, emergency wording, and cross-account access. A code change or passing CI alone does not prove the deployed Qwen endpoint is reachable.
