# SolenOS Qwen on Railway

This deployment keeps SolenOS's FastAPI service and Qwen inference runtime as **separate Railway services**. The backend calls Ollama's native `POST /api/chat` endpoint over Railway's private network. It does not use a hosted Qwen API or Qwen API key.

## Railway service setup

### 1. Create the Ollama service

In the same Railway project and environment as the SolenOS backend:

1. Add a service from this repository.
2. Configure its Dockerfile path as `deploy/ollama/Dockerfile` (repository root as the build context).
3. Name the service `ollama` so the reference variable below resolves as written.
4. Attach a persistent volume mounted at `/root/.ollama`. This stores model files across restarts and redeployments.
5. Set `OLLAMA_MODEL=qwen2.5:1.5b` on the Ollama service for the initial CPU-constrained deployment. The startup script pulls that model if it is missing.
6. Keep the Ollama service private. Do not generate a public domain for port 11434.

The container binds Ollama to `0.0.0.0:11434` inside its service so the backend can reach it through Railway private networking. The volume is important: without it, the model may need to be downloaded again after redeployments.

### 2. Configure the SolenOS backend service

Set these variables on the **backend service**, not just on the Ollama service:

- `OLLAMA_BASE_URL=http://${{ollama.RAILWAY_PRIVATE_DOMAIN}}:11434`
- `OLLAMA_MODEL=qwen2.5:1.5b`
- `OLLAMA_TIMEOUT_SECONDS=60`

Railway resolves the reference to the private hostname for the service named `ollama`. If you name the service differently, update the reference accordingly. Do not set the backend's `OLLAMA_BASE_URL` to `127.0.0.1`, `localhost`, or the developer's computer.

The backend rejects loopback and public Ollama URLs in every environment and treats them as **not configured**. It accepts only the private Railway service hostname. This prevents the app from pretending its model connection is configured when the URL only points back at the backend container. Capture persistence can still report its own status; Companion/extraction must explicitly report that Qwen is not configured or unavailable.

### 3. Resource and model choice

Railway does not provide GPU instances in its Open WebUI guide. A small quantized model is therefore a starting point, not a quality guarantee. Begin with `qwen2.5:1.5b` to limit memory pressure, then evaluate accuracy, latency, and memory against SolenOS's care-specific test set before using it with caregivers. Do not upgrade to a larger model without confirming the service has sufficient RAM and CPU. The model tag is configurable and can be changed on both services.

## Required verification before enabling production use

1. Confirm the Ollama service deploys and its logs show the configured model pull completed.
2. From the backend service, confirm `OLLAMA_BASE_URL` resolves to the private Ollama hostname.
3. Make an authenticated Companion request and confirm the response reports `provider_status=connected` and `ai_generated=true` only when an actual model response was received.
4. Submit test capture evidence and confirm extraction candidates are `pending_review`, linked to verbatim source evidence, and never auto-written as authoritative facts.
5. Test a missing model, stopped Ollama service, timeout, malformed model output, and emergency wording. Failures must not generate substitute answers.
6. Confirm backend and Ollama services restart while the model remains available from the persistent volume.
7. Verify logs and error responses do not expose secrets or care-record content.

The shared Qwen gateway is the single backend transport used by Companion, Daily Capture extraction, text-bearing document extraction, Community peer-experience summaries, and read-only LCR explanations. Community output is restricted to published peer posts and never writes to the LCR. Text-bearing documents can be analyzed only after their text has been extracted and linked to a saved care event; uploaded images currently remain without OCR and therefore cannot be analyzed as document text.

The shared Qwen gateway is the single backend transport used by Companion, Daily Capture extraction, text-bearing document extraction, Community peer-experience summaries, and read-only LCR explanations. Community output is restricted to published peer posts and never writes to the LCR. Text-bearing documents can be analyzed only after their text has been extracted and linked to a saved care event; uploaded images currently remain without OCR and therefore cannot be analyzed as document text.\n\nMocked CI tests validate request contracts only; they do not prove Railway connectivity or a live model invocation. This repository change prepares the runtime image and backend configuration, but it does not create Railway services, attach volumes, set service variables, or deploy the services in the Railway dashboard.
