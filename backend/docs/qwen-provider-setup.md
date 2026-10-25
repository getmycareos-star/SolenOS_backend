# Qwen through the bundled Railway runtime

SolenOS runs the open-weight `qwen2.5:1.5b` model through Ollama's native HTTP API. Ollama and the model weights are bundled into the **existing SolenOS_backend Docker image** and run beside FastAPI in the same Railway container.

## Cost and hosting boundary

- No hosted Qwen API, paid inference API, Qwen API key, or external model provider is used.
- Inference does not run on the founder's Dell, in the browser, or on another personal computer.
- No second Railway model service is required by this configuration.
- The model runs on Railway's existing backend service. Railway compute, image storage, and deployment resource usage still follow the existing Railway plan; this avoids a separate Qwen API subscription, not the hosting provider's ordinary resource costs.

## Runtime configuration

The Docker entrypoint `start-with-qwen.sh` sets the following defaults inside the Railway container:

- `OLLAMA_BASE_URL=http://127.0.0.1:11434`
- `OLLAMA_MODEL=qwen2.5:1.5b`
- `OLLAMA_SIDECAR_ENABLED=true`
- `OLLAMA_TIMEOUT_SECONDS=60` (gateway default)
- bounded inference settings: `OLLAMA_NUM_CTX=1536`, `OLLAMA_NUM_PREDICT=96`

Here, `127.0.0.1` means the **same Railway container**, where the bundled Ollama process is started by the entrypoint. It does not refer to a developer's machine. The gateway only permits this production loopback connection when the bundled-runtime flag is set; without that flag, production loopback configuration is rejected. Non-loopback model URLs must use an allowed private Railway hostname.

The Docker build downloads Ollama, pulls `qwen2.5:1.5b`, and fails if the model cannot be installed. At startup, the entrypoint verifies that Ollama is responding and that the model is present before starting FastAPI. If either process exits, the container exits instead of silently serving a fake AI response.

## API and safety behavior

- The backend calls Ollama's native `POST /api/chat` endpoint and reads `message.content`.
- It does not fall back to Gemini, OpenAI, hosted Qwen, or hardcoded conversational replies when inference fails.
- Companion uses Qwen-generated natural language. Greetings and small talk remain conversation messages, not Living Care Record (LCR) events.
- Emergency-like language follows the explicit emergency safety path rather than relying on Qwen to identify an emergency.
- Read-only Qwen assistance receives only the authorized context needed for the selected workflow.
- Community posts remain personal peer reports, not authoritative care facts.
- Daily Capture saves the care event first. Qwen candidate extraction is a separate request after the server returns the saved event ID.
- Candidate observations require exact source quotes and are persisted as `pending_review`. Empty extraction is reported as `no_candidates`; unavailable inference is reported explicitly. Neither case creates an LCR fact.
- Accept/reject decisions record caregiver attribution. Candidate review does not automatically promote or modify authoritative LCR facts.

## Verification gates

The backend CI builds the production Docker image with the actual model, starts the bundled Ollama + FastAPI runtime, checks the authenticated runtime inventory, sends real inference requests for Companion and all supported read-only workflows, tests Community summary and document context, and verifies Daily Capture candidate persistence/review without LCR writes.

CI proves the model works in the built image. It does not prove that the production Railway deployment is healthy. Production is considered verified only after the database migration succeeds, Railway reports the deployment live and `/ready` is healthy, and authenticated live inference and persistence checks pass.
