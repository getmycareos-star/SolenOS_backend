#!/usr/bin/env bash
set -Eeuo pipefail

export OLLAMA_HOST="127.0.0.1:11434"
export OLLAMA_MODELS="${OLLAMA_MODELS:-/root/.ollama/models}"
export OLLAMA_KEEP_ALIVE="${OLLAMA_KEEP_ALIVE:-30m}"
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
ollama list | grep -q 'qwen2.5:0.5b' || {
  echo "Required bundled Qwen model is missing; refusing to start without inference." >&2
  exit 1
}

python -m uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8080}" &
uvicorn_pid=$!
set +e
wait -n "$ollama_pid" "$uvicorn_pid"
status=$?
set -e
exit "$status"
