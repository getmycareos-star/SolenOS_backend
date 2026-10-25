#!/usr/bin/env bash
set -Eeuo pipefail

export OLLAMA_HOST="127.0.0.1:11434"
# Disable Ollama cloud routing explicitly; all inference must remain in this container.
export OLLAMA_NO_CLOUD="true"
export OLLAMA_MODELS="${OLLAMA_MODELS:-/root/.ollama/models}"
export OLLAMA_KEEP_ALIVE="${OLLAMA_KEEP_ALIVE:-30m}"
# Keep the startup warm-up and API inference on the same runner configuration.
# Otherwise Ollama may reload the model for a different context length on the
# first caregiver request and transiently return 503 while the runner restarts.
export OLLAMA_CONTEXT_LENGTH="${OLLAMA_NUM_CTX:-1536}"
# The model shares the Railway container with FastAPI. Serialize inference and
# keep one loaded model so parallel requests cannot multiply KV-cache memory.
export OLLAMA_NUM_PARALLEL="${OLLAMA_NUM_PARALLEL:-1}"
export OLLAMA_MAX_LOADED_MODELS="${OLLAMA_MAX_LOADED_MODELS:-1}"
export OLLAMA_MAX_QUEUE="${OLLAMA_MAX_QUEUE:-8}"
export OLLAMA_BASE_URL="${OLLAMA_BASE_URL:-http://127.0.0.1:11434}"
export OLLAMA_MODEL="${OLLAMA_MODEL:-qwen2.5:1.5b}"
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
ollama list | grep -q 'qwen2.5:1.5b' || {
  echo "Required bundled Qwen model is missing; refusing to start without inference." >&2
  exit 1
}

# Warm the actual bundled model before FastAPI reports readiness. Ollama's
# /api/tags can list a model while its inference engine is still loading it;
# without this warm-up the first caregiver greeting may receive a false 503.
warm=0
for attempt in $(seq 1 60); do
  if ollama run "$OLLAMA_MODEL" "Reply with the single word READY." >/tmp/qwen-warmup.log 2>&1; then
    warm=1
    break
  fi
  if ! kill -0 "$ollama_pid" 2>/dev/null; then
    cat /tmp/ollama-runtime.log >&2 || true
    echo "Ollama exited during Qwen warm-up." >&2
    exit 1
  fi
  sleep 1
done
if [[ "$warm" -ne 1 ]]; then
  cat /tmp/qwen-warmup.log >&2 || true
  cat /tmp/ollama-runtime.log >&2 || true
  echo "Bundled Qwen model did not complete its startup inference; refusing to report readiness." >&2
  exit 1
fi

python -m uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8080}" &
uvicorn_pid=$!
set +e
wait -n "$ollama_pid" "$uvicorn_pid"
status=$?
set -e
exit "$status"
