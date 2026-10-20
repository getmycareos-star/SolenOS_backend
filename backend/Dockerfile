FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install the OCR executable used by the backend and the compiler needed by
# dependencies that may ship native extensions.
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

# Hatchling's configured wheel package is "app", so source must be present
# when pip builds the project.
COPY pyproject.toml ./
COPY app ./app
RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir .

# Include migrations, static assets and runtime support files.
COPY . .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
