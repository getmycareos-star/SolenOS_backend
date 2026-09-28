"""Probe which locally pulled models can actually load and answer.

Run directly:  python -m scripts.model_probe [model ...]
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request

from app.services.llm.client import OllamaClient


def probe(model: str, timeout: float = 600.0) -> dict:
    client = OllamaClient(model=model)
    body = {
        "model": model,
        "prompt": 'Reply with JSON only: {"ok": true}',
        "format": "json",
        "stream": False,
    }
    request = urllib.request.Request(
        f"{client.base_url}/api/generate",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:220]
        except Exception:
            detail = ""
        return {"model": model, "ok": False, "error": detail, "seconds": round(time.monotonic() - started, 1)}
    except Exception as exc:
        return {"model": model, "ok": False, "error": str(exc)[:220], "seconds": round(time.monotonic() - started, 1)}

    return {
        "model": model,
        "ok": True,
        "seconds": round(time.monotonic() - started, 1),
        "load_seconds": round(float(payload.get("load_duration", 0)) / 1e9, 1),
        "response": str(payload.get("response", ""))[:80],
    }


def main(argv: list[str]) -> int:
    models = argv[1:] or OllamaClient().list_models()
    for model in models:
        result = probe(model)
        print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
