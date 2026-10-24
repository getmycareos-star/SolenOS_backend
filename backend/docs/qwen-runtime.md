# SolenOS local Qwen runtime

## Runtime contract

- SolenOS uses the open-weight `qwen2.5:1.5b` model through Ollama's native `POST /api/chat` endpoint.
- The model is bundled into the existing Railway backend image and runs as a local sidecar process in the same container. It is not running on the founder's Dell and does not call a hosted Qwen/OpenAI/Gemini inference API.
- The model weights are downloaded during image build and included in the deployment image. Runtime inference uses `http://127.0.0.1:11434` only because Ollama and FastAPI share that Railway container.
- The application permits production loopback only when `OLLAMA_SIDECAR_ENABLED=true`, which is set by `start-with-qwen.sh`. Otherwise, production accepts only a private `*.railway.internal` Ollama hostname. Public model endpoints are rejected.
- No API key is needed. This avoids hosted-model API charges; Railway compute/build usage still follows the existing Railway plan.

## Qwen's authority boundary

Qwen is a support layer. It may generate Companion language, summarize published peer experiences, propose source-grounded observations, organize appointment questions, and help structure handoff notes. It may not diagnose, prescribe, claim causes from patterns, invent source quotes, or modify the Living Care Record.

Care capture is persisted before Qwen analysis runs. A Qwen timeout or invalid response cannot make a saved care write appear failed or cause the client to submit the same care update again. Candidate extraction validates each source quote as an exact substring of saved source text and labels results `pending_review`; it does not promote candidates into authoritative facts.

Conversation history is stored separately from care evidence. Community posts are non-authoritative peer reports. Emergency-like messages bypass model inference and use the established safety response.

## Deployment

The backend Railway service root directory is `/backend`. `railway.json` selects the Dockerfile builder, runs Alembic pre-deploy, and starts `start-with-qwen.sh`. The container refuses to start the API if Ollama or the bundled model is missing. Readiness remains governed by the backend database/schema check at `/ready`.

The model is not exposed through a public domain. FastAPI and Ollama share the private container loopback. Verify the actual deployment with:
1. `GET /health` and `GET /ready` returning 200.
2. A real authorized `POST /api/v1/conversation/message` greeting that returns model-generated natural language.
3. `POST /api/v1/assistant/extract-candidates` for a persisted event, confirming every candidate quote appears verbatim in the source.
4. `POST /api/v1/care-delta/reconcile` returning a model-backed result rather than 503.
5. A second anonymous session being denied access to the first session's event/evidence.
