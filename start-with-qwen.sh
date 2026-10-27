#!/usr/bin/env bash
set -Eeuo pipefail

# Keep the bundled same-container Ollama runtime and gateway configuration aligned.
# Railway can override these explicitly, but the bundled sidecar must work by default.
export OLLAMA_BASE_URL="${OLLAMA_BASE_URL:-http://127.0.0.1:11434}"
export OLLAMA_SIDECAR_ENABLED="${OLLAMA_SIDECAR_ENABLED:-true}"
export OLLAMA_MODEL="${OLLAMA_MODEL:-qwen2.5:0.5b}"

# Run the bundled Ollama service and FastAPI in the same Railway container.
ollama serve >/tmp/ollama-runtime.log 2>&1 &
OLLAMA_PID=$!
cleanup() { kill "$OLLAMA_PID" 2>/dev/null || true; }
trap cleanup EXIT INT TERM

ready=0
for attempt in $(seq 1 60); do
  if curl -fsS http://127.0.0.1:11434/api/tags >/dev/null; then
    ready=1
    break
  fi
  if ! kill -0 "$OLLAMA_PID" 2>/dev/null; then
    cat /tmp/ollama-runtime.log >&2
    echo "Ollama exited before becoming ready." >&2
    exit 1
  fi
  sleep 2
done
if [ "$ready" -ne 1 ]; then
  cat /tmp/ollama-runtime.log >&2
  echo "Ollama did not become ready within 120 seconds." >&2
  exit 1
fi

# Do not infer model readiness from `ollama list`: a manifest can appear while
# its blobs are still downloading. Pull/verify the configured model before
# starting FastAPI so the API cannot advertise readiness while inference returns 503.
ollama pull "$OLLAMA_MODEL"
if ! ollama show "$OLLAMA_MODEL" >/dev/null 2>&1; then
  echo "Configured Qwen model is not fully available after pull: $OLLAMA_MODEL" >&2
  exit 1
fi

exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8080}"
