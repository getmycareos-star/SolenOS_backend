#!/usr/bin/env bash
set -Eeuo pipefail

# Core caregiver APIs must remain available even if the optional local model
# sidecar cannot start or download its model. Companion inference may report
# unavailable independently; consent, authentication and records must not hang.
export OLLAMA_HOST="${OLLAMA_HOST:-127.0.0.1:11434}"
export OLLAMA_MODELS="${OLLAMA_MODELS:-/root/.ollama/models}"
export OLLAMA_KEEP_ALIVE="${OLLAMA_KEEP_ALIVE:-30m}"
export OLLAMA_BASE_URL="${OLLAMA_BASE_URL:-http://127.0.0.1:11434}"
export OLLAMA_MODEL="${OLLAMA_MODEL:-qwen2.5:0.5b}"

ollama serve >/tmp/ollama-runtime.log 2>&1 &
OLLAMA_PID=$!
UVICORN_PID=""

cleanup() {
  if [[ -n "$UVICORN_PID" ]]; then kill "$UVICORN_PID" 2>/dev/null || true; fi
  kill "$OLLAMA_PID" 2>/dev/null || true
  wait "$OLLAMA_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# Bound the sidecar startup wait. Do not block FastAPI on optional inference.
for attempt in $(seq 1 10); do
  if curl -fsS http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then break; fi
  if ! kill -0 "$OLLAMA_PID" 2>/dev/null; then
    echo "Optional Ollama sidecar did not start; starting core APIs without it." >&2
    break
  fi
  sleep 1
done

# Attempt a background model pull only when Ollama is ready. The API starts
# immediately; no network download is allowed to block caregiver workflows.
if curl -fsS http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
  (
    if ! ollama list 2>/dev/null | grep -q 'qwen2.5:0.5b'; then
      ollama pull qwen2.5:0.5b || echo "Optional Qwen model pull failed; core APIs remain available." >&2
    fi
  ) >/tmp/qwen-model-pull.log 2>&1 &
fi

python -m uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8080}" &
UVICORN_PID=$!
wait "$UVICORN_PID"
