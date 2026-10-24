FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    OLLAMA_MODELS=/root/.ollama/models

# Install only runtime dependencies, then install Ollama's published Linux
# archive directly. The upstream install.sh runs its own package-manager
# mutations, which made this Railway image build fail before the model stage.
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends bash ca-certificates curl libgomp1 tini zstd; \
    curl --retry 4 --retry-all-errors --connect-timeout 20 -fsSL \
      https://ollama.com/download/ollama-linux-amd64.tar.zst -o /tmp/ollama.tar.zst; \
    tar --zstd -xf /tmp/ollama.tar.zst -C /usr; \
    rm -f /tmp/ollama.tar.zst; \
    rm -rf /var/lib/apt/lists/*; \
    ollama --version

WORKDIR /app
COPY pyproject.toml ./
COPY app ./app
COPY alembic.ini ./alembic.ini
COPY alembic ./alembic
RUN python -m pip install --upgrade pip && python -m pip install .

# Download the open-weight Qwen model during image build so production startup
# does not depend on the Dell, an API key, or a runtime model-download step.
# Model weights are part of the image; Railway build/image usage remains subject
# to the existing plan and usage limits.
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
