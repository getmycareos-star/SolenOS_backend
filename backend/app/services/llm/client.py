"""Local-only Ollama client.

Deliberately built on urllib from the standard library. The backend has no HTTP
client dependency today and adding one would mean a new install requirement for a
component that must never leave the machine, so the smallest possible surface is
the right one.

Every method returns a (ok, payload_or_error) shape instead of raising, because
the language model is an optional enrichment and a failure to reach it must never
take down a care endpoint.
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from app.core.config import get_settings
from app.core.llm_enums import LLMStatus


class OllamaClient:
    GENERATE_PATH = "/api/generate"
    TAGS_PATH = "/api/tags"
    HEALTH_PATH = "/"

    def __init__(
        self,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> None:
        settings = get_settings()
        self.base_url = (base_url or settings.llm_base_url).rstrip("/")
        self.model = model or settings.llm_model
        self.timeout = timeout if timeout is not None else settings.llm_timeout_seconds

    def is_available(self) -> Tuple[bool, Optional[str]]:
        """Report whether the local runtime is reachable and the model is pulled."""
        ok, payload, error = self._request("GET", self.TAGS_PATH, timeout=min(self.timeout, 5.0))
        if not ok:
            return False, error or "local model runtime is not reachable"
        if not isinstance(payload, dict):
            return False, "unexpected tags payload"
        names = []
        for entry in payload.get("models") or []:
            if isinstance(entry, dict) and entry.get("name"):
                names.append(str(entry["name"]))
        if not names:
            return False, "ollama is running but no models are pulled"
        if not self._model_present(names):
            return False, f"model '{self.model}' is not pulled (available: {', '.join(names)})"
        return True, None

    def list_models(self) -> List[str]:
        ok, payload, _ = self._request("GET", self.TAGS_PATH, timeout=min(self.timeout, 5.0))
        if not ok or not isinstance(payload, dict):
            return []
        return [
            str(entry["name"])
            for entry in (payload.get("models") or [])
            if isinstance(entry, dict) and entry.get("name")
        ]

    def _model_present(self, names: List[str]) -> bool:
        if self.model in names:
            return True
        # Ollama reports "qwen3-coder:30b"; a bare "qwen3-coder" should also match.
        base = self.model.split(":")[0]
        return any(name == base or name.split(":")[0] == base for name in names)

    def generate_json(
        self,
        system_prompt: str,
        user_payload: Dict[str, Any],
    ) -> Tuple[bool, Optional[Dict[str, Any]], str, Dict[str, Any]]:
        """Ask the model for a single JSON object.

        Returns (ok, parsed_or_None, raw_text, meta). meta always carries the
        latency and token counts so the audit row can record what actually ran,
        even on failure.
        """
        import time

        settings = get_settings()
        body = {
            "model": self.model,
            "prompt": _render_user_payload(user_payload),
            "system": system_prompt,
            "format": "json",
            "stream": False,
            "options": {
                "temperature": settings.llm_temperature,
                "num_ctx": settings.llm_context_tokens,
                "num_predict": settings.llm_max_output_tokens,
            },
        }
        meta: Dict[str, Any] = {
            "latency_ms": 0,
            "prompt_tokens": None,
            "completion_tokens": None,
        }
        started = time.monotonic()
        ok, payload, error = self._request(
            "POST",
            self.GENERATE_PATH,
            payload=body,
            timeout=self.timeout,
        )
        meta["latency_ms"] = int((time.monotonic() - started) * 1000)

        if not ok or not isinstance(payload, dict):
            detail = error or "no response from local model"
            status = (
                LLMStatus.TIMEOUT
                if "timed out" in detail.lower() or "timeout" in detail.lower()
                else LLMStatus.UNAVAILABLE
            )
            return False, None, detail, {**meta, "status": status.value}

        raw = payload.get("response")
        if not isinstance(raw, str) or not raw.strip():
            return False, None, raw or "", {**meta, "status": LLMStatus.INVALID_RESPONSE.value}

        meta["prompt_tokens"] = payload.get("prompt_eval_count")
        meta["completion_tokens"] = payload.get("eval_count")

        parsed = extract_json_object(raw)
        if parsed is None:
            return False, None, raw, {**meta, "status": LLMStatus.INVALID_RESPONSE.value}
        return True, parsed, raw, {**meta, "status": LLMStatus.OK.value}

    def _request(
        self,
        method: str,
        path: str,
        payload: Optional[Dict[str, Any]] = None,
        timeout: Optional[float] = None,
    ) -> Tuple[bool, Any, Optional[str]]:
        url = f"{self.base_url}{path}"
        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout or self.timeout) as response:
                body = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            return False, None, _http_error_detail(exc, path)
        except socket.timeout:
            return False, None, f"timed out contacting {path} (model may still be loading)"
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, socket.timeout):
                return False, None, f"timed out contacting {path} (model may still be loading)"
            return False, None, f"cannot reach local model: {exc.reason}"
        except (OSError, ValueError) as exc:
            return False, None, f"local model request failed: {exc}"

        try:
            return True, json.loads(body), None
        except json.JSONDecodeError:
            return False, None, f"non-JSON response from {path}"


def _http_error_detail(exc: urllib.error.HTTPError, path: str) -> str:
    """Ollama puts the real reason in the body, not the status line.

    A 500 from /api/generate is almost always the model failing to load, and the
    body says so precisely: out of memory, missing weights, wrong quantisation.
    Discarding it turns an actionable error into "http 500".
    """
    detail = ""
    try:
        body = exc.read().decode("utf-8", errors="replace")
        if body:
            try:
                parsed = json.loads(body)
                detail = str(parsed.get("error") or body)
            except json.JSONDecodeError:
                detail = body
    except Exception:
        detail = ""
    detail = detail.strip().replace("\n", " ")[:300]
    if detail:
        return f"http {exc.code} from {path}: {detail}"
    return f"http {exc.code} from {path}"


def _render_user_payload(user_payload: Dict[str, Any]) -> str:
    """Render untrusted structured input as clearly fenced data.

    Caregiver text, document text and model output from earlier stages are all
    untrusted. Fencing them in a data block means instructions found inside that
    text sit in a place the system prompt has already told the model to ignore.
    """
    sections = []
    for key, value in user_payload.items():
        if value is None:
            continue
        if isinstance(value, (dict, list)):
            rendered = json.dumps(value, ensure_ascii=False, default=str)
        else:
            rendered = str(value)
        sections.append(f"{key}:\n{rendered}")
    body = "\n\n".join(sections)
    return f"<untrusted_data>\n{body}\n</untrusted_data>"


def extract_json_object(raw: str):
    """Recover a JSON object from a model response.

    Local models wrap JSON in prose or fences often enough that a bare
    json.loads is a real failure source. This only ever returns a dict.
    """
    if not raw:
        return None
    text = raw.strip()

    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()

    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    while start != -1:
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(text)):
            char = text[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start : index + 1]
                    try:
                        parsed = json.loads(candidate)
                    except json.JSONDecodeError:
                        break
                    if isinstance(parsed, dict):
                        return parsed
                    break
        start = text.find("{", start + 1)
    return None
