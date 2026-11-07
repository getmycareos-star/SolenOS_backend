FROM python:3.12-slim-bookworm

# Railway build context must be the repository root: COPY paths below are rooted here.

# Production runtime and CI must use the same bundled Qwen model; see backend/docs/qwen-provider-setup.md.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    OLLAMA_MODELS=/root/.ollama/models

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends bash ca-certificates curl libgomp1 tini zstd tesseract-ocr tesseract-ocr-eng; \
    curl --retry 4 --retry-all-errors --connect-timeout 20 -fsSL \
      https://ollama.com/download/ollama-linux-amd64.tar.zst -o /tmp/ollama.tar.zst; \
    tar --zstd -xf /tmp/ollama.tar.zst -C /usr; \
    rm -f /tmp/ollama.tar.zst; \
    rm -rf /var/lib/apt/lists/*; \
    ollama --version

WORKDIR /app
COPY backend/pyproject.toml ./pyproject.toml
COPY backend/app ./app
COPY backend/alembic.ini ./alembic.ini
COPY backend/alembic ./alembic
RUN python -m pip install --upgrade pip && python -m pip install .

# Keep Ollama in the image, but download the model at runtime via start-with-qwen.sh.
# Exporting the model layer during the image build stalled production deployment.
RUN ollama --version

COPY start-with-qwen.sh /app/start-with-qwen.sh
EXPOSE 8080
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["/bin/bash", "/app/start-with-qwen.sh"]
