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
    elif not (loopback or host.endswith(".railway.internal") or host.endswith(".internal") or host == "ollama"):
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



def extract_review_candidates(
    source_text: str,
    *,
    source_event_id: str,
    source_evidence_id: str | None = None,
) -> dict:
    """Return only verbatim-grounded, explicitly review-required candidate observations."""
    bounded_source = (source_text or "").strip()[:12_000]
    if not bounded_source:
        return {"observations": [], "open_questions": []}

    messages = [
        {
            "role": "system",
            "content": (
                "You extract possible caregiver-observed facts from a source, but you are not the authority. "
                "Return valid JSON only with exactly this shape: "
                '{"observations":[{"subject":"string","observation":"string","source_quote":"exact verbatim substring from source"}],'
                '"open_questions":["string"]}. '
                "Only include observations explicitly supported by the source. Do not infer diagnosis, cause, risk, "
                "medication changes, or facts not stated. Every source_quote must be copied exactly from the source. "
                "If there are no supported observations, return an empty observations array. Ask at most three short "
                "clarifying questions. Never treat greetings or small talk as care observations."
            ),
        },
        {"role": "user", "content": "Source text to review:\n" + bounded_source},
    ]
    raw = qwen_chat(messages)
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=503,
            detail="Qwen returned an invalid candidate structure; no candidate was returned.",
        ) from exc
    if not isinstance(parsed, dict) or not isinstance(parsed.get("observations"), list):
        raise HTTPException(
            status_code=503,
            detail="Qwen returned an invalid candidate structure; no candidate was saved.",
        )

    observations = []
    for item in parsed["observations"][:12]:
        if not isinstance(item, dict):
            continue
        subject = item.get("subject")
        observation = item.get("observation")
        quote = item.get("source_quote")
        if not all(isinstance(value, str) and value.strip() for value in (subject, observation, quote)):
            continue
        if quote not in bounded_source:
            continue
        if any(term in observation.casefold() for term in ("diagnosed with", "caused by", "should increase dose", "should stop medication")):
            continue
        candidate = {
            "subject": subject.strip()[:120],
            "observation": observation.strip()[:500],
            "source_quote": quote,
            "review_status": "pending_review",
            "source_event_id": source_event_id,
        }
        if source_evidence_id:
            candidate["source_evidence_id"] = source_evidence_id
        observations.append(candidate)

    questions = parsed.get("open_questions", [])
    if not isinstance(questions, list):
        questions = []
    clean_questions = [
        question.strip()[:300]
        for question in questions[:3]
        if isinstance(question, str) and question.strip()
    ]
    return {"observations": observations, "open_questions": clean_questions}



def qwen_runtime_status() -> dict:
    """Confirm the configured model is actually served by the local Ollama runtime."""
    base, model, timeout = get_ollama_config()
    request = urllib.request.Request(
        base + "/api/tags",
        headers={"Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=min(timeout, 10.0)) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        raise HTTPException(
            status_code=503,
            detail="SolenOS local Qwen runtime is not responding.",
        ) from exc
    models = payload.get("models")
    if not isinstance(models, list):
        raise HTTPException(status_code=503, detail="Ollama returned an invalid model inventory.")
    names = [str(item.get("name", "")) for item in models if isinstance(item, dict)]
    if model not in names:
        raise HTTPException(
            status_code=503,
            detail="The configured Qwen model is not installed in the local runtime.",
        )
    return {
        "available": True,
        "model": model,
        "provider": "local_ollama",
        "inference_api": "ollama_native_http",
        "hosted_inference_api": False,
        "runtime": "bundled_railway_sidecar" if os.getenv("OLLAMA_SIDECAR_ENABLED", "").casefold() == "true" else "private_railway_service",
    }
