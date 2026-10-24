FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    OLLAMA_MODELS=/root/.ollama/models

# Keep OS package installation separate from Ollama installation so a failed
# build reports which operation actually failed. Pin Bookworm rather than
# following the moving python:3.12-slim Debian release.
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends bash ca-certificates curl tini; \
    rm -rf /var/lib/apt/lists/*

# Install Ollama in its own build step. Do not hide installer failures behind
# an apt-get command chain.
RUN set -eux; \
    curl --fail --show-error --location https://ollama.com/install.sh -o /tmp/ollama-install.sh; \
    sh /tmp/ollama-install.sh; \
    rm -f /tmp/ollama-install.sh; \
    ollama --version

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
