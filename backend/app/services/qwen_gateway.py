"""SolenOS-owned Qwen gateway.

The model is a support capability, never an authority. This gateway only talks to
Ollama's native HTTP API; it never calls a hosted inference provider.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from urllib.parse import urlparse

from fastapi import HTTPException


def get_ollama_config() -> tuple[str, str, float]:
    base = os.getenv("OLLAMA_BASE_URL", "").strip().rstrip("/")
    model = os.getenv("OLLAMA_MODEL", "qwen2.5:1.5b").strip()
    try:
        timeout = max(5.0, min(float(os.getenv("OLLAMA_TIMEOUT_SECONDS", "60")), 180.0))
    except (TypeError, ValueError):
        timeout = 60.0

    if not base or not model:
        raise HTTPException(
            status_code=503,
            detail="SolenOS local Qwen is unavailable; no model response was generated.",
        )
    parsed = urlparse(base)
    host = (parsed.hostname or "").casefold()
    if parsed.scheme != "http" or not host:
        raise HTTPException(
            status_code=503,
            detail="SolenOS local Qwen must use the private Ollama HTTP service.",
        )

    sidecar_enabled = os.getenv("OLLAMA_SIDECAR_ENABLED", "").casefold() == "true"
    loopback = host in {"localhost", "127.0.0.1", "::1"}
    environment = (
        os.getenv("RAILWAY_ENVIRONMENT_NAME")
        or os.getenv("RAILWAY_ENVIRONMENT")
        or os.getenv("APP_ENV")
        or os.getenv("ENVIRONMENT")
        or ""
    ).strip().casefold()
    if environment in {"production", "prod"}:
        if loopback and not sidecar_enabled:
            raise HTTPException(
                status_code=503,
                detail="Loopback Qwen is accepted only for the bundled Railway sidecar.",
            )
        if not loopback and not host.endswith(".railway.internal"):
            raise HTTPException(
                status_code=503,
                detail="SolenOS accepts only its bundled Qwen runtime or a private Railway model service.",
            )
    elif not (loopback or host.endswith(".railway.internal")):
        raise HTTPException(
            status_code=503,
            detail="SolenOS local Qwen must use a loopback or private Railway service.",
        )
    return base, model, timeout


def qwen_chat(messages: list[dict], *, timeout: float | None = None) -> str:
    base, model, configured_timeout = get_ollama_config()
    body = json.dumps(
        {"model": model, "messages": messages, "stream": False},
        ensure_ascii=False,
    ).encode("utf-8")
    request = urllib.request.Request(
        base + "/api/chat",
        data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout or configured_timeout) as response:
            result = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        raise HTTPException(
            status_code=503,
            detail="SolenOS local Qwen is unavailable; no model response was generated.",
        ) from exc
    content = result.get("message", {}).get("content")
    if not isinstance(content, str) or not content.strip():
        raise HTTPException(
            status_code=503,
            detail="SolenOS local Qwen returned no usable response.",
        )
    return content.strip()
