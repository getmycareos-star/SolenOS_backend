# Qwen through Ollama

SolenOS Companion and Daily Capture extraction use **Qwen through Ollama's native HTTP API**. They do not use a hosted Qwen provider, OpenAI-compatible `/chat/completions` endpoint, bearer token, or Qwen API key.

## Runtime configuration

The backend supports two local Ollama topologies. Both use the native `POST /api/chat` endpoint, read `message.content`, and send no `Authorization` header or Qwen API key.

### Current Railway production topology: bundled sidecar

The current Railway backend container starts Ollama from `start-with-qwen.sh` alongside FastAPI. In this mode:

- `OLLAMA_SIDECAR_ENABLED=true`
- `OLLAMA_BASE_URL=http://127.0.0.1:11434`
- `OLLAMA_MODEL=qwen3.5:4b`

Loopback is accepted in production only when `OLLAMA_SIDECAR_ENABLED=true`; it refers to the Ollama process inside the same backend container, not a developer's computer. Railway logs have confirmed the bundled Ollama process and a successful native `POST /api/chat` response. Model inventory can become available before the model runner has finished loading, so inventory is not proof of inference readiness.

### Optional topology: separate private Railway Ollama service

If Ollama is moved to a separate Railway service, disable the bundled sidecar and use that service's private hostname:

- `OLLAMA_SIDECAR_ENABLED=false`
- `OLLAMA_BASE_URL=http://<service-name>.railway.internal:11434`
- `OLLAMA_MODEL=qwen3.5:4b`

Production rejects public model URLs. Do not point the backend at a public Ollama endpoint.

The gateway defaults are `OLLAMA_TIMEOUT_SECONDS=180`, `OLLAMA_NUM_CTX=8192`, `OLLAMA_NUM_PREDICT=96`, and `OLLAMA_NUM_THREADS=2`; explicit Railway values override these defaults. Companion uses a separate bounded request timeout: `SOLENOS_COMPANION_TIMEOUT_SECONDS` defaults to 120 seconds and is capped at 180 seconds. The caregiver-facing request budget is 210 seconds.

No Qwen API key is needed or read. The integration does not fall back to Gemini, OpenAI, or a hosted Qwen endpoint if local Ollama is unavailable. CI and production use the same model name and native API contract, but their process topologies differ: CI uses a dedicated Ollama service container while Railway production currently bundles Ollama in the backend container. Real production acceptance is therefore a separate release gate.

## Railway deployment

Do not interpret `127.0.0.1` as a remote model service. It is valid only for the bundled sidecar mode described above. For a separate Railway Ollama service, use its `.railway.internal` hostname and set `OLLAMA_SIDECAR_ENABLED=false`. The backend rejects public model URLs in production.

The repository defines the container startup and runtime checks; it does not create Railway services, set production variables, attach Railway volumes, or deploy production services. Verify the actual Railway source revision, variable names, volume, and deployment logs before changing the topology.

## How the three parts of SolenOS fit together

The product has three complementary parts with distinct responsibilities:

- **Living Care Record (LCR) — the continuity foundation.** The LCR is the organized, longitudinal history of recorded care information. Backend record APIs and persistence remain authoritative for saved events, evidence, appointments, and related care facts. The backend must enforce record-scoped authorization and preserve source and review status.
- **Daily Care — capture and correction.** Daily capture and add-record flows accept caregiver-reported observations and proposed corrections. Extracted or proposed information must retain its source and review state; an unsaved draft is not a saved record, and a candidate is not automatically an authoritative fact.
- **AI Companion — support and preparation.** Companion helps respond to a caregiver's request and can use relevant, authorized care context. Its generated conversation and suggestions remain separate from authoritative LCR data unless a supported, explicit caregiver-reviewed record flow saves information.

The user-facing mental model is: **Capture it in Daily Care. Understand it in the Living Care Record. Work through it with AI Companion.** These are not three competing sources of truth. The LCR is the record; Daily Care is a capture/update path; Companion is a support layer.

## Runtime behavior and safety

- Emergency-like language bypasses model inference and uses the existing emergency safety response.
- If Ollama is unavailable or returns invalid output, Companion never marks the response as AI-generated. It may return a deterministic safety response or an exact, source-grounded excerpt only when the authorized record supports it; otherwise it returns an explicit unavailable response. The frontend labels every non-Qwen reply so it cannot be mistaken for successful model inference.
- Companion receives only the selected, authorized care recipient's context and filters model-returned evidence IDs against evidence supplied to the model.
- Daily Capture extraction requires candidate source quotes to match saved evidence verbatim.
- Extracted observations remain `pending_review`; they are not automatically written as authoritative Living Care Record facts.
- Companion conversation messages remain separate from the Living Care Record.

## Verification

Use an authenticated caregiver session for the checks below:

1. `GET /api/v1/assistant/installation` reports runtime reachability and model inventory only. It must not claim inference is available merely because the model appears in `/api/tags`.
2. `GET /api/v1/assistant/readiness` remains an inventory/readiness signal and must not run a hidden model request.
3. `GET /api/v1/assistant/status` reports `available=true` only after a recent successful generation.
4. `POST /api/v1/conversation/message` must return `ai_generated=true` for a Qwen-generated answer, `written_to_lcr=false`, and `persistence=conversation_only`. A deterministic source or safety response must remain `ai_generated=false` and visibly labelled in the frontend.
5. `POST /api/v1/assistant/assist` must return a real model response with `ai_generated=true`, `review_required=true`, `written_to_lcr=false`, and `persistence=read_only_assistance` for supported read-only workflows.
6. Test cold model loading, concurrent authenticated caregivers, missing models, timeouts, long prompts, document extraction, emergency wording, cross-caregiver isolation, and recovery after an inference failure. Confirm failed inference does not remove the original upload or saved caregiver event.

Mocked CI responses verify request contracts only. Isolated live-Qwen CI verifies real inference in the CI runtime. Neither substitutes for the deployed Railway acceptance workflow, which must verify the exact deployed source commit and real authenticated production inference.
