#!/usr/bin/env bash
set -Eeuo pipefail

python -m uvicorn app.main:app --host 127.0.0.1 --port 8011 >/tmp/solenos-uvicorn.log 2>&1 &
server_pid=$!
cleanup() {
  kill "$server_pid" 2>/dev/null || true
}
trap cleanup EXIT

ready=0
for attempt in $(seq 1 20); do
  if curl --silent --fail http://127.0.0.1:8011/health >/dev/null &&
     curl --silent --fail http://127.0.0.1:8011/ready >/dev/null; then
    ready=1
    break
  fi
  sleep 1
done
if [ "$ready" != "1" ]; then
  cat /tmp/solenos-uvicorn.log
  exit 1
fi

python -m pytest -q
