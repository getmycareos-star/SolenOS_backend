FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    OLLAMA_MODELS=/root/.ollama/models

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends bash ca-certificates curl libgomp1 tini zstd; \
    rm -rf /var/lib/apt/lists/*

RUN set -eux; \
    curl --retry 4 --retry-all-errors --connect-timeout 20 --fail --show-error --location \
      https://ollama.com/download/ollama-linux-amd64.tar.zst -o /tmp/ollama.tar.zst; \
    tar --zstd -xf /tmp/ollama.tar.zst -C /usr; \
    rm -f /tmp/ollama.tar.zst; \
    ollama --version

WORKDIR /app
COPY backend/pyproject.toml ./pyproject.toml
COPY backend/app ./app
COPY backend/alembic.ini ./alembic.ini
COPY backend/alembic ./alembic
RUN python -m pip install --upgrade pip && python -m pip install .

# Bundle the open-weight model into the existing Railway backend image.
# No Dell process, external inference API, API key or separate service is used.
RUN set -eux; \
    ollama serve >/tmp/ollama-build.log 2>&1 & server_pid=$!; \
    trap 'kill "$server_pid" 2>/dev/null || true' EXIT; \
    ready=0; \
    for attempt in $(seq 1 90); do \
      if curl -fsS http://127.0.0.1:11434/api/tags >/dev/null; then ready=1; break; fi; \
      sleep 2; \
    done; \
    if [ "$ready" -ne 1 ]; then cat /tmp/ollama-build.log >&2; exit 1; fi; \
    ollama pull qwen2.5:1.5b; \
    ollama list | grep -q 'qwen2.5:1.5b'; \
    kill "$server_pid" 2>/dev/null || true

COPY start-with-qwen.sh /app/start-with-qwen.sh
EXPOSE 8080
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["/bin/bash", "/app/start-with-qwen.sh"]
