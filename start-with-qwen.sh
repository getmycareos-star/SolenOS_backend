#!/usr/bin/env bash
set -Eeuo pipefail

# Core caregiver APIs must start independently of optional local inference.
# Ollama is a best-effort sidecar; model download or startup failure must not
# prevent FastAPI from serving authentication, care records, and readiness.
export OLLAMA_BASE_URL="${OLLAMA_BASE_URL:-http://127.0.0.1:11434}"
export OLLAMA_SIDECAR_ENABLED="${OLLAMA_SIDECAR_ENABLED:-true}"
export OLLAMA_MODEL="${OLLAMA_MODEL:-qwen2.5:0.5b}"
export OLLAMA_HOST="${OLLAMA_HOST:-127.0.0.1:11434}"
export OLLAMA_MODELS="${OLLAMA_MODELS:-/root/.ollama/models}"
export OLLAMA_KEEP_ALIVE="${OLLAMA_KEEP_ALIVE:-30m}"

ollama serve >/tmp/ollama-runtime.log 2>&1 &
OLLAMA_PID=$!
UVICORN_PID=""

cleanup() {
  if [[ -n "$UVICORN_PID" ]]; then kill "$UVICORN_PID" 2>/dev/null || true; fi
  kill "$OLLAMA_PID" 2>/dev/null || true
  wait "$OLLAMA_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# Bound sidecar startup to 10 seconds. FastAPI startup must not depend on it.
OLLAMA_READY=0
for attempt in $(seq 1 10); do
  if curl -fsS "$OLLAMA_BASE_URL/api/tags" >/dev/null 2>&1; then
    OLLAMA_READY=1
    break
  fi
  if ! kill -0 "$OLLAMA_PID" 2>/dev/null; then
    echo "Optional Ollama sidecar did not start; core APIs will start without inference." >&2
    break
  fi
  sleep 1
done

# Model pull is deliberately asynchronous. A missing model may make Companion
# inference unavailable temporarily, but must not block care-record operations.
if [[ "$OLLAMA_READY" == "1" ]]; then
  (
    if ! ollama show "$OLLAMA_MODEL" >/dev/null 2>&1; then
      ollama pull "$OLLAMA_MODEL" || echo "Optional model pull failed; core APIs remain available." >&2
    fi
  ) >/tmp/qwen-model-pull.log 2>&1 &
fi

python -m uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8080}" &
UVICORN_PID=$!
wait "$UVICORN_PID"
