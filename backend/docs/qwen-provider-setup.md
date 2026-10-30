# Qwen through Ollama

SolenOS Companion and Daily Capture extraction use **Qwen through Ollama's native HTTP API**. They do not use a hosted Qwen provider, OpenAI-compatible `/chat/completions` endpoint, bearer token, or Qwen API key.

## Runtime configuration

The production Railway backend currently runs Ollama as a sidecar in the **same container** as FastAPI. The startup script configures:

- `OLLAMA_BASE_URL=http://127.0.0.1:11434`
- `OLLAMA_SIDECAR_ENABLED=true`
- `OLLAMA_MODEL=qwen3.5:4b`
- `OLLAMA_MODELS=/root/.ollama/models`

The gateway also accepts a private Railway Ollama service URL ending in `.railway.internal` when a separate service is intentionally configured. It rejects public hosted inference URLs and does not fall back to Gemini, OpenAI, or a hosted Qwen endpoint.

Qwen 3.5 4B (Q4_K_M) is the proposed production candidate, replacing the previous Qwen 2.5 0.5B default. Its Ollama registry download is approximately 3.4 GB; this is not the full runtime memory requirement. The Railway service has 8 GB RAM and 8 vCPUs, but actual peak memory and inference latency must be measured before promotion. The shared gateway defaults to a 4,096-token context and 256-token generation budget, both bounded and configurable through environment variables. These settings still require live latency and memory validation on Railway.

## Railway deployment and persistence

The bundled sidecar stores model files under `/root/.ollama/models`. Without a Railway volume mounted at `/root/.ollama`, model files live on the container's ephemeral filesystem and may need to be downloaded again after a redeployment. A persistent volume would preserve downloaded model layers across deployments, but it has a recurring storage charge and must be explicitly approved before creation.

The startup script deliberately starts FastAPI even if Ollama or the model pull fails. Therefore, the backend's `/ready` health check alone does not prove that Qwen is installed or serving inference. Verify the configured model through the authenticated Qwen runtime-status endpoint and a real authenticated Companion request.

No Qwen API key is needed or read by this integration.

## How the three parts of SolenOS fit together

The product has three complementary parts with distinct responsibilities:

- **Living Care Record (LCR) — the continuity foundation.** The LCR is the organized, longitudinal history of recorded care information. Backend record APIs and persistence remain authoritative for saved events, evidence, appointments, and related care facts. The backend must enforce record-scoped authorization and preserve source and review status.
- **Daily Care — capture and correction.** Daily capture and add-record flows accept caregiver-reported observations and proposed corrections. Extracted or proposed information must retain its source and review state; an unsaved draft is not a saved record, and a candidate is not automatically an authoritative fact.
- **AI Companion — support and preparation.** Companion helps respond to a caregiver's request and can use relevant, authorized care context. Its generated conversation and suggestions remain separate from authoritative LCR data unless a supported, explicit caregiver-reviewed record flow saves information.

The user-facing mental model is: **Capture it in Daily Care. Understand it in the Living Care Record. Work through it with AI Companion.** These are not three competing sources of truth. The LCR is the record; Daily Care is a capture/update path; Companion is a support layer.

## Runtime behavior and safety

- Emergency-like language bypasses model inference and uses the existing emergency safety response.
- If Ollama is unavailable or returns invalid output, Companion reports an explicit provider error rather than inventing a substitute answer.
- Companion receives only the selected, authorized care recipient's context and filters model-returned evidence IDs against evidence supplied to the model.
- Daily Capture extraction requires candidate source quotes to match saved evidence verbatim.
- Extracted observations remain `pending_review`; they are not automatically written as authoritative Living Care Record facts.
- Companion conversation messages remain separate from the Living Care Record.

## Verification

After Ollama is running and the configured model is present, verify an authorized test account through `POST /api/v1/conversation/message` and submit a test capture through the supported capture route. Confirm Companion reports `provider_status=connected` and `ai_generated=true` only after a real model response; capture extraction should report `pending_review` only when grounded candidates are actually returned. Test missing models, timeouts, emergency wording, and cross-account access.

Mocked CI responses verify request contracts only; they do not prove the model is installed or reachable. Before production use, confirm the live Railway service can make a real inference request and that the model remains available after restart.
