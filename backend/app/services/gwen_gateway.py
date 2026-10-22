"""Single Railway-hosted Gwen/Qwen inference gateway for all SolenOS modules.

All model traffic uses Ollama's native /api/chat endpoint. Module services supply
their own scoped context and policy; this transport layer never reads or writes care
records and never falls back to a hosted provider.
"""
import json
from typing import Any
from urllib.request import Request, urlopen

from app.core.config import get_settings


def call_gwen_json(
    *,
    system_prompt: str,
    user_prompt: str,
    history: list[dict[str, str]] | None = None,
    temperature: float = 0.0,
) -> tuple[dict[str, Any] | None, str]:
    """Call the configured Railway-private Ollama model and parse a JSON object.

    Returns (payload, status), where status is connected, not_configured, or error.
    Errors intentionally contain no source text, credentials, or provider response.
    """
    settings = get_settings()
    if not settings.ollama_configured:
        return None, "not_configured"

    safe_history = [
        {"role": item["role"], "content": item["content"][:4000]}
        for item in (history or [])[-12:]
        if item.get("role") in {"user", "assistant"}
        and isinstance(item.get("content"), str)
    ]
    request = Request(
        f"{settings.ollama_base_url.strip().rstrip('/')}/api/chat",
        data=json.dumps({
            "model": settings.ollama_model.strip(),
            "messages": [
                {"role": "system", "content": system_prompt},
                *safe_history,
                {"role": "user", "content": user_prompt},
            ],
            "temperature": max(0.0, min(float(temperature), 0.3)),
            "stream": False,
            "format": "json",
        }, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=max(3.0, min(settings.ollama_timeout_seconds, 120.0))) as response:
            body = json.loads(response.read().decode("utf-8"))
        content = body["message"]["content"]
        parsed = json.loads(content)
        if not isinstance(parsed, dict):
            return None, "error"
        return parsed, "connected"
    except Exception:
        # Do not leak request content or model/provider error bodies into API responses.
        return None, "error"
