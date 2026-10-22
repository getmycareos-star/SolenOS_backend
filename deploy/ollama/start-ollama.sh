#!/bin/sh
set -eu

: "${OLLAMA_HOST:=0.0.0.0:11434}"
: "${OLLAMA_MODEL:=qwen2.5:1.5b}"
export OLLAMA_HOST

ollama serve &
OLLAMA_PID=$!

cleanup() {
  kill "$OLLAMA_PID" 2>/dev/null || true
}
trap cleanup INT TERM EXIT

attempt=0
until ollama list >/dev/null 2>&1; do
  attempt=$((attempt + 1))
  if [ "$attempt" -ge 60 ]; then
    echo "Ollama did not become ready within 120 seconds." >&2
    exit 1
  fi
  sleep 2
done

echo "Ensuring configured Ollama model is present: $OLLAMA_MODEL"
ollama pull "$OLLAMA_MODEL"

wait "$OLLAMA_PID"
