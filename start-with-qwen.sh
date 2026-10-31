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
export QWEN_INSTALL_STATE_FILE="${QWEN_INSTALL_STATE_FILE:-/tmp/qwen-installation-status.json}"
export OLLAMA_KEEP_ALIVE="${OLLAMA_KEEP_ALIVE:-30m}"

write_qwen_state() {
  local state="$1"
  local reason="${2:-}"
  QWEN_STATE="$state" QWEN_REASON="$reason" QWEN_MODEL="$OLLAMA_MODEL" QWEN_STATE_FILE="$QWEN_INSTALL_STATE_FILE" python - <<'PY'
import datetime, json, os, tempfile
path = os.environ["QWEN_STATE_FILE"]
payload = {
    "state": os.environ["QWEN_STATE"],
    "model": os.environ["QWEN_MODEL"],
    "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "failure_reason": os.environ.get("QWEN_REASON") or None,
}
directory = os.path.dirname(path) or "."
os.makedirs(directory, exist_ok=True)
fd, temporary = tempfile.mkstemp(prefix=".qwen-state-", dir=directory)
try:
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)
    os.replace(temporary, path)
finally:
    if os.path.exists(temporary):
        os.unlink(temporary)
PY
}

write_qwen_state "not_started"
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
    if ollama show "$OLLAMA_MODEL" >/dev/null 2>&1; then
      write_qwen_state "installed"
    else
      write_qwen_state "downloading"
      if ollama pull "$OLLAMA_MODEL"; then
        if ollama show "$OLLAMA_MODEL" >/dev/null 2>&1; then
          write_qwen_state "installed"
        else
          write_qwen_state "failed" "model_missing_after_successful_pull"
        fi
      else
        write_qwen_state "failed" "ollama_pull_failed"
      fi
    fi
  ) >/tmp/qwen-model-pull.log 2>&1 &
else
  write_qwen_state "failed" "ollama_runtime_not_ready"
fi

python -m uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8080}" &
UVICORN_PID=$!
wait "$UVICORN_PID"
