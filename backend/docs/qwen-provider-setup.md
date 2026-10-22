# Qwen through Ollama

SolenOS Companion, free-text and structured Daily Capture, text-bearing document extraction, Community peer summaries, and read-only LCR explanations use the shared **Qwen gateway through Ollama's native HTTP API**. They do not use a hosted Qwen provider, OpenAI-compatible `/chat/completions` endpoint, bearer token, or Qwen API key.

## Runtime configuration

The default `OLLAMA_BASE_URL` is empty. SolenOS rejects loopback addresses such as `127.0.0.1` and `localhost` in every environment, so the backend never silently connects to a developer's desktop Ollama process. Configure the private Railway service hostname and model on the backend service:

- `OLLAMA_BASE_URL=http://${{ollama.RAILWAY_PRIVATE_DOMAIN}}:11434`
- `OLLAMA_MODEL=qwen2.5:1.5b`
- `OLLAMA_TIMEOUT_SECONDS=60`

No Qwen API key is needed or read by this integration.

## Railway deployment

**Do not use the local loopback URL in Railway.** Railway's backend container cannot reach Ollama on a developer's computer through `127.0.0.1`; loopback refers to the backend container itself.

For the Railway setup, follow [railway-qwen-deployment.md](railway-qwen-deployment.md). It provides a separate Ollama service image, persistent model storage instructions, and the required private service URL. The backend service must set:

- `OLLAMA_BASE_URL=http://${{ollama.RAILWAY_PRIVATE_DOMAIN}}:11434`
- `OLLAMA_MODEL=qwen2.5:1.5b`
- `OLLAMA_TIMEOUT_SECONDS=60`

The backend treats loopback and public model URLs as not configured in every environment; only a Railway private service URL is accepted. This prevents an incorrect localhost value from being treated as a valid remote model connection. This repository does not itself create Railway services, configure Railway variables, attach volumes, or deploy services.

## Runtime behavior and safety

- Emergency-like language bypasses model inference and uses the existing emergency safety response.
- If Ollama is unavailable or returns invalid output, Companion reports an explicit provider error rather than inventing a substitute answer.
- Companion receives only the selected, authorized care recipient's context and filters model-returned evidence IDs against evidence supplied to the model.
- Daily Capture extraction requires candidate source quotes to match saved evidence verbatim.
- Extracted observations remain `pending_review`; they are not automatically written as authoritative Living Care Record facts.
- Companion conversation messages remain separate from the Living Care Record.

## Verification

After the Railway Ollama service is running and the configured model is present, verify an authorized test account through `POST /api/v1/conversation/message`, `POST /api/v1/community/experience/summary`, `POST /api/v1/lcr/explain`, and the supported capture/document routes. Confirm Companion reports `provider_status=connected` and `ai_generated=true` only after a real model response; capture extraction should report `pending_review` only when grounded candidates are actually returned. Test missing models, timeouts, emergency wording, and cross-account access.

Mocked CI responses verify request contracts only; they do not prove the model is installed or reachable. Before production use, confirm the live Railway service can make a real inference request and that the model remains available after restart.
