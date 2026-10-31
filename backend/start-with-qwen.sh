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

# Publish atomic, machine-readable model installation state for the API.
write_install_state() {
  local state="$1"
  local reason="${2:-}"
  QWEN_INSTALL_STATE="$state" QWEN_INSTALL_MODEL="$OLLAMA_MODEL" QWEN_INSTALL_FAILURE="$reason" \
    python - <<'PY'
import json
import os
from datetime import datetime, timezone

path = os.environ.get("QWEN_INSTALL_STATE_FILE", "/tmp/qwen-installation-status.json")
payload = {
    "state": os.environ["QWEN_INSTALL_STATE"],
    "model": os.environ["QWEN_INSTALL_MODEL"],
    "updated_at": datetime.now(timezone.utc).isoformat(),
    "failure_reason": os.environ.get("QWEN_INSTALL_FAILURE") or None,
}
temporary = path + ".tmp"
with open(temporary, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, separators=(",", ":"))
    handle.flush()
    os.fsync(handle.fileno())
os.replace(temporary, path)
PY
}

# Bound sidecar startup without blocking the core caregiver API.
OLLAMA_READY=false
for attempt in $(seq 1 10); do
  if curl -fsS http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
    OLLAMA_READY=true
    break
  fi
  if ! kill -0 "$OLLAMA_PID" 2>/dev/null; then
    echo "Optional Ollama sidecar did not start; starting core APIs without it." >&2
    break
  fi
  sleep 1
done

if [[ "$OLLAMA_READY" == "true" ]]; then
  (
    # The configured model, not a hard-coded tag, is the source of truth.
    if ollama list 2>/dev/null | awk 'NR > 1 { print $1 }' | grep -Fqx -- "$OLLAMA_MODEL"; then
      echo "Configured Qwen model already installed: $OLLAMA_MODEL"
      write_install_state "installed"
    else
      echo "Pulling configured Qwen model: $OLLAMA_MODEL"
      write_install_state "downloading"
      if ollama pull "$OLLAMA_MODEL" && ollama list 2>/dev/null | awk 'NR > 1 { print $1 }' | grep -Fqx -- "$OLLAMA_MODEL"; then
        write_install_state "installed"
        echo "Configured Qwen model installed: $OLLAMA_MODEL"
      else
        write_install_state "failed" "ollama_pull_failed"
        echo "Optional Qwen model pull failed for $OLLAMA_MODEL; core APIs remain available." >&2
      fi
    fi
  ) >/tmp/qwen-model-pull.log 2>&1 &
else
  write_install_state "failed" "ollama_sidecar_unavailable"
fi

python -m uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8080}" &
UVICORN_PID=$!
wait "$UVICORN_PID"
