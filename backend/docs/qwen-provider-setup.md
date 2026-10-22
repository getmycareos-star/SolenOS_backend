# Local Qwen through Ollama

SolenOS Companion and Daily Capture extraction use **local Qwen inference through Ollama's native HTTP API**. They do not use a hosted Qwen provider, OpenAI-compatible `/chat/completions` endpoint, bearer token, or Qwen API key.

## Local runtime configuration

Ollama must be running in the same host/network namespace as the backend, or at a private Ollama endpoint reachable from the backend. The defaults are:

- `OLLAMA_BASE_URL=http://127.0.0.1:11434`
- `OLLAMA_MODEL=qwen2.5:7b`
- `OLLAMA_TIMEOUT_SECONDS=60` (optional; request timeout is bounded)

Install/pull the intended Qwen model in Ollama before starting the application. For example, run `ollama pull qwen2.5:7b` in a terminal where Ollama is installed. If your local Qwen model has a different exact tag, set `OLLAMA_MODEL` to the tag shown by `ollama list`. No Qwen API key is needed or read by this integration.

The backend calls Ollama's native `POST /api/chat` endpoint and reads `message.content`. It sends no `Authorization` header. It does not fall back to Gemini, OpenAI, or a hosted Qwen endpoint if local Ollama is unavailable.

## Important deployment boundary

A backend running on Railway cannot reach Ollama at `127.0.0.1` on a developer's computer: localhost refers to the Railway container itself. Local inference therefore works only when the backend and Ollama share a host/network namespace, or when the backend is intentionally configured to reach a private, secured Ollama host. Do not expose Ollama publicly without a proper network security boundary. This source change does not make the remote Railway backend able to reach a developer's laptop automatically.

## Runtime behavior and safety

- Emergency-like language bypasses model inference and uses the existing emergency safety response.
- If Ollama is unavailable or returns invalid output, Companion reports an explicit provider error rather than inventing a substitute answer.
- Companion receives only the selected, authorized care recipient's context and filters model-returned evidence IDs against evidence supplied to the model.
- Daily Capture extraction requires candidate source quotes to match saved evidence verbatim.
- Extracted observations remain `pending_review`; they are not automatically written as authoritative Living Care Record facts.
- Companion conversation messages remain separate from the Living Care Record.

## Verification

After starting Ollama and ensuring the configured model exists, verify a signed-in test account through `POST /api/v1/conversation/message` and submit a test capture through `POST /api/situation` or `POST /api/v1/daily/log`. Confirm Companion reports `provider_status=connected`, capture reports `qwen_extraction_status=pending_review` when grounded candidates are found, and the request targets `/api/chat` without an Authorization header. Test missing models, timeouts, emergency wording, and cross-account access. CI with mocked Ollama responses does not prove the local model is installed or reachable at runtime.
