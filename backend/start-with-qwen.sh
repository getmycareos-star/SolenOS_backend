#!/usr/bin/env bash
set -Eeuo pipefail

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

if ! ollama list | grep -q 'qwen2.5:0.5b'; then
  ollama pull qwen2.5:0.5b
fi

exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8080}"
