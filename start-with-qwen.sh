#!/usr/bin/env bash
set -Eeuo pipefail

export OLLAMA_HOST="127.0.0.1:11434"
# Disable cloud routing explicitly: all inference must remain in this container.
export OLLAMA_NO_CLOUD="true"
export OLLAMA_MODELS="${OLLAMA_MODELS:-/root/.ollama/models}"
export OLLAMA_KEEP_ALIVE="${OLLAMA_KEEP_ALIVE:-30m}"
# Keep warm-up and API inference on the same runner context.
export OLLAMA_CONTEXT_LENGTH="${OLLAMA_NUM_CTX:-1536}"
# The model shares the Railway container with FastAPI. Serialize inference and
# keep one loaded model so parallel requests cannot multiply KV-cache memory.
export OLLAMA_NUM_PARALLEL="${OLLAMA_NUM_PARALLEL:-1}"
export OLLAMA_MAX_LOADED_MODELS="${OLLAMA_MAX_LOADED_MODELS:-1}"
export OLLAMA_MAX_QUEUE="${OLLAMA_MAX_QUEUE:-8}"
export OLLAMA_BASE_URL="${OLLAMA_BASE_URL:-http://127.0.0.1:11434}"
export OLLAMA_MODEL="${OLLAMA_MODEL:-qwen2.5:0.5b}"
export OLLAMA_SIDECAR_ENABLED="true"

ollama serve > >(tee /tmp/ollama-runtime.log >&2) 2>&1 &
ollama_pid=$!
uvicorn_pid=""

shutdown() {
  [[ -n "$uvicorn_pid" ]] && kill "$uvicorn_pid" 2>/dev/null || true
  kill "$ollama_pid" 2>/dev/null || true
  wait "$ollama_pid" 2>/dev/null || true
}
trap shutdown EXIT INT TERM

for attempt in $(seq 1 60); do
  if ! kill -0 "$ollama_pid" 2>/dev/null; then
    cat /tmp/ollama-runtime.log >&2 || true
    echo "Ollama exited before becoming ready." >&2
    exit 1
  fi
  if curl -fsS http://127.0.0.1:11434/api/tags >/dev/null; then break; fi
  sleep 1
done
curl -fsS http://127.0.0.1:11434/api/tags >/dev/null || {
  cat /tmp/ollama-runtime.log >&2 || true
  echo "Bundled Ollama runtime did not become ready." >&2
  exit 1
}
ollama list | grep -Fq "$OLLAMA_MODEL" || {
  echo "Required bundled Qwen model ($OLLAMA_MODEL) is missing; refusing to start without inference." >&2
  exit 1
}

# /api/tags can list a model before its inference runner is warm. Complete a
# real local inference before starting FastAPI, so readiness never advertises
# a usable assistant while the first request would fail during model loading.
# Bound the inference itself: a hung local runner must not block startup forever
# while Railway's /ready health check repeatedly receives service-unavailable.
warmup_timeout="${QWEN_WARMUP_TIMEOUT_SECONDS:-180}"
if ! timeout --signal=TERM --kill-after=10s "${warmup_timeout}s" \
  ollama run "$OLLAMA_MODEL" "Reply with the single word READY." >/tmp/qwen-warmup.log 2>&1; then
  cat /tmp/qwen-warmup.log >&2 || true
  cat /tmp/ollama-runtime.log >&2 || true
  echo "Bundled Qwen model failed or timed out during startup inference; refusing to report readiness." >&2
  exit 1
fi
if ! grep -qi 'READY' /tmp/qwen-warmup.log; then
  cat /tmp/qwen-warmup.log >&2 || true
  echo "Bundled Qwen startup inference returned no expected READY response; refusing to report readiness." >&2
  exit 1
fi

python -m uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8080}" &
uvicorn_pid=$!
set +e
wait -n "$ollama_pid" "$uvicorn_pid"
status=$?
set -e
exit "$status"
