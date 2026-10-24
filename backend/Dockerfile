FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    OLLAMA_MODELS=/root/.ollama/models

# Keep OS package installation and Ollama installation in separate build steps:
# a failure now reports the exact failing command instead of masking it in one RUN.
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends bash ca-certificates curl tini; \
    rm -rf /var/lib/apt/lists/*

RUN set -eux; \
    curl --fail --show-error --location --retry 3 --retry-all-errors \
      https://ollama.com/install.sh --output /tmp/ollama-install.sh; \
    bash /tmp/ollama-install.sh; \
    rm -f /tmp/ollama-install.sh

WORKDIR /app
COPY pyproject.toml ./
COPY app ./app
COPY alembic.ini ./alembic.ini
COPY alembic ./alembic
RUN python -m pip install --upgrade pip && python -m pip install .

# Bundle the open-weight model into the deployment image. No hosted inference API,
# desktop process, runtime model download, or separate model service is required.
RUN set -eux; \
    ollama serve >/tmp/ollama-build.log 2>&1 & server_pid=$!; \
    trap 'kill "$server_pid" 2>/dev/null || true' EXIT; \
    for attempt in $(seq 1 90); do \
      if curl -fsS http://127.0.0.1:11434/api/tags >/dev/null; then break; fi; \
      sleep 2; \
    done; \
    curl -fsS http://127.0.0.1:11434/api/tags >/dev/null; \
    ollama pull qwen2.5:1.5b; \
    ollama list | grep -q 'qwen2.5:1.5b'

COPY start-with-qwen.sh /app/start-with-qwen.sh
EXPOSE 8080
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["/bin/bash", "/app/start-with-qwen.sh"]
