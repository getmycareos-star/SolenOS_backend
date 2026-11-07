"""SolenOS-owned Qwen gateway.

The model is a support capability, never an authority. This gateway only talks to
Ollama's native HTTP API; it never calls a hosted inference provider.
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

from fastapi import HTTPException

logger = logging.getLogger(__name__)
_READINESS_CACHE: dict[str, object] = {"checked_at": 0.0, "result": None}


def get_ollama_config() -> tuple[str, str, float]:
    base = os.getenv("OLLAMA_BASE_URL", "").strip().rstrip("/")
    model = os.getenv("OLLAMA_MODEL", "qwen3.5:4b").strip()
    try:
        timeout = max(5.0, min(float(os.getenv("OLLAMA_TIMEOUT_SECONDS", "180")), 180.0))
    except (TypeError, ValueError):
        timeout = 180.0

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


def qwen_chat(
    messages: list[dict],
    *,
    timeout: float | None = None,
    response_format: dict | str | None = None,
    options: dict | None = None,
) -> str:
    started = time.monotonic()
    try:
        base, model, configured_timeout = get_ollama_config()
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        _log_inference_result(os.getenv("OLLAMA_MODEL", "qwen3.5:4b"), started, timeout or 0.0, "configuration_unavailable", detail.get("code", "ollama_configuration_invalid"))
        raise
    # Keep inference memory bounded for Railway deployments. The 4B model's
    # default context window can allocate a large KV cache and destabilize the
    # same container that hosts FastAPI. Every workflow uses the shared gateway,
    # so the limit applies consistently without changing model authority.
    try:
        num_ctx = max(1024, min(int(os.getenv("OLLAMA_NUM_CTX", "1024")), 8192))
    except (TypeError, ValueError):
        num_ctx = 1024
    try:
        num_predict = max(64, min(int(os.getenv("OLLAMA_NUM_PREDICT", "96")), 1024))
    except (TypeError, ValueError):
        num_predict = 96
    # Railway production is CPU-only; Ollama otherwise detected 48 threads in a
    # constrained container and real requests stalled. Bound per-request CPU
    # parallelism so inference does not oversubscribe the available vCPUs.
    try:
        num_thread = max(1, min(int(os.getenv("OLLAMA_NUM_THREADS", "2")), 8))
    except (TypeError, ValueError):
        num_thread = 2
    bounded_options = {
        "num_ctx": num_ctx,
        "num_predict": num_predict,
        "num_thread": num_thread,
        "temperature": 0.2,
    }
    if options:
        bounded_options.update(options)
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "keep_alive": os.getenv("OLLAMA_KEEP_ALIVE", "30m"),
        "options": bounded_options,
    }
    # Qwen 3.x models expose a separate reasoning channel. On CPU-constrained
    # Railway, that channel can consume the generation budget without producing
    # user-visible message.content. Companion requires an answer, not a hidden
    # reasoning trace, so explicitly disable thinking for Qwen 3.x only.
    if model.casefold().startswith("qwen3"):
        payload["think"] = False
    if response_format is not None:
        payload["format"] = response_format
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        base + "/api/chat",
        data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    started = time.monotonic()
    failure_category = None
    failure_reason = None
    try:
        with urllib.request.urlopen(request, timeout=timeout or configured_timeout) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        failure_category = "upstream_http_error"
        failure_reason = f"http_{exc.code}"
        # Ollama uses HTTP 404 for an absent model. Preserve this actionable
        # operational distinction without exposing upstream response contents.
        if exc.code == 404:
            try:
                upstream_error = json.loads(exc.read().decode("utf-8")).get("error", "")
            except (ValueError, UnicodeError, OSError):
                upstream_error = ""
            if "model" in str(upstream_error).casefold() and any(
                marker in str(upstream_error).casefold()
                for marker in ("not found", "not installed", "does not exist")
            ):
                failure_category = "model_not_installed"
                failure_reason = "configured_model_missing"
                _log_inference_result(model, started, timeout or configured_timeout, failure_category, failure_reason)
                raise HTTPException(
                    status_code=503,
                    detail={"code": failure_category, "reason": failure_reason, "message": "SolenOS AI assistance is unavailable."},
                ) from exc
        _log_inference_result(model, started, timeout or configured_timeout, failure_category, failure_reason)
        raise HTTPException(
            status_code=503,
            detail={"code": failure_category, "reason": failure_reason, "message": "SolenOS AI assistance is unavailable."},
        ) from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        failure_category, failure_reason = "invalid_upstream_response", type(exc).__name__
        _log_inference_result(model, started, timeout or configured_timeout, failure_category, failure_reason)
        raise HTTPException(status_code=502, detail={"code": failure_category, "reason": failure_reason, "message": "SolenOS AI assistance is unavailable."}) from exc
    except TimeoutError as exc:
        failure_category, failure_reason = "inference_timeout", "socket_timeout"
        _log_inference_result(model, started, timeout or configured_timeout, failure_category, failure_reason)
        raise HTTPException(status_code=504, detail={"code": failure_category, "reason": failure_reason, "message": "SolenOS AI assistance is unavailable."}) from exc
    except urllib.error.URLError as exc:
        if isinstance(getattr(exc, "reason", None), TimeoutError):
            failure_category, failure_reason = "inference_timeout", "url_timeout"
            _log_inference_result(model, started, timeout or configured_timeout, failure_category, failure_reason)
            raise HTTPException(status_code=504, detail={"code": failure_category, "reason": failure_reason, "message": "SolenOS AI assistance is unavailable."}) from exc
        failure_category, failure_reason = "runtime_unreachable", type(getattr(exc, "reason", exc)).__name__
        _log_inference_result(model, started, timeout or configured_timeout, failure_category, failure_reason)
        raise HTTPException(status_code=503, detail={"code": failure_category, "reason": failure_reason, "message": "SolenOS AI assistance is unavailable."}) from exc
    except (ValueError, OSError) as exc:
        failure_category, failure_reason = "runtime_error", type(exc).__name__
        _log_inference_result(model, started, timeout or configured_timeout, failure_category, failure_reason)
        raise HTTPException(status_code=503, detail={"code": failure_category, "reason": failure_reason, "message": "SolenOS AI assistance is unavailable."}) from exc
    content = result.get("message", {}).get("content")
    if not isinstance(content, str) or not content.strip():
        failure_category, failure_reason = "empty_model_response", "missing_message_content"
        _log_inference_result(model, started, timeout or configured_timeout, failure_category, failure_reason)
        raise HTTPException(status_code=502, detail={"code": failure_category, "reason": failure_reason, "message": "SolenOS AI assistance is unavailable."})
    _log_inference_result(model, started, timeout or configured_timeout, None, None)
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
    # Ollama's native JSON mode avoids fragile prompt-only formatting while
    # keeping source-quote validation as the final authority boundary.
    raw = qwen_chat(messages, response_format="json", options={"temperature": 0, "num_predict": 384})
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





def extract_companion_care_candidates(source_text: str) -> dict:
    """Identify possible care observations; every returned candidate is an exact source quotation."""
    source = (source_text or "").strip()[:4000]
    if not source:
        return {"observations": []}
    raw = qwen_chat(
        [
            {
                "role": "system",
                "content": (
                    "Identify whether the caregiver's own message contains meaningful new information "
                    "about an actual person's care, condition, experience, or observed behaviour. "
                    "Do not classify greetings, ordinary conversation, general questions, advice requests, "
                    "hypotheticals, or discussion of a medical topic without a personal care observation. "
                    "Return JSON only in this exact shape: "
                    '{"observations":[{"source_quote":"exact verbatim substring from the caregiver message","event_time_quote":"exact verbatim time phrase from that quotation or empty string"}]}. '
                    "Every source_quote and non-empty event_time_quote must be copied character-for-character from the message. "
                    "Do not paraphrase, summarize, infer, diagnose, infer causes, add dates or quantities, "
                    "or create clinical conclusions. Return an empty observations array when no meaningful "
                    "care information is explicitly reported. Do not use examples or fixed phrase matching."
                ),
            },
            {"role": "user", "content": source},
        ],
        response_format="json",
        options={"temperature": 0, "num_predict": 256},
    )
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=503, detail="Care-update identification returned invalid structured output") from exc
    if not isinstance(parsed, dict) or not isinstance(parsed.get("observations"), list):
        raise HTTPException(status_code=503, detail="Care-update identification returned an invalid structure")
    observations = []
    seen = set()
    for item in parsed["observations"][:8]:
        quote = item.get("source_quote") if isinstance(item, dict) else None
        if not isinstance(quote, str):
            continue
        quote = quote.strip()
        if len(quote) < 8 or quote not in source or quote in seen:
            continue
        event_time_quote = item.get("event_time_quote") if isinstance(item.get("event_time_quote"), str) else ""
        event_time_quote = event_time_quote.strip()
        if event_time_quote and (event_time_quote not in quote or event_time_quote not in source):
            event_time_quote = ""
        seen.add(quote)
        observations.append({"source_quote": quote, "event_time_quote": event_time_quote or None})
    return {"observations": observations}


def _log_inference_result(model: str, started: float, timeout: float, error_category: str | None, failure_reason: str | None) -> None:
    """Emit metadata only. Never include prompts, replies, identifiers, or care content."""
    elapsed_ms = round((time.monotonic() - started) * 1000)
    logger.info(json.dumps({
        "event": "qwen_inference",
        "model": model,
        "duration_ms": elapsed_ms,
        "timeout_seconds": timeout,
        "timed_out": error_category == "inference_timeout",
        "upstream_error_category": error_category,
        "failure_reason": failure_reason,
        "outcome": "failed" if error_category else "succeeded",
    }, separators=(",", ":")))


def qwen_installation_status() -> dict:
    """Read the atomic startup state file without exposing host paths or log contents."""
    state_path = os.getenv("QWEN_INSTALL_STATE_FILE", "/tmp/qwen-installation-status.json")
    try:
        with open(state_path, "r", encoding="utf-8") as handle:
            state = json.load(handle)
        status = state.get("state")
        if status not in {"not_started", "downloading", "installed", "failed"}:
            raise ValueError("invalid_state")
        return {
            "state": status,
            "model": str(state.get("model") or get_ollama_config()[1]),
            "updated_at": state.get("updated_at"),
            "failure_reason": state.get("failure_reason") if status == "failed" else None,
        }
    except FileNotFoundError:
        return {"state": "not_started", "model": os.getenv("OLLAMA_MODEL", "qwen3.5:4b"), "updated_at": None, "failure_reason": None}
    except Exception:
        logger.exception("Could not read Qwen installation state")
        return {"state": "failed", "model": os.getenv("OLLAMA_MODEL", "qwen3.5:4b"), "updated_at": None, "failure_reason": "state_file_unreadable"}


def qwen_readiness_status(force: bool = False) -> dict:
    """Report the last real inference result without running inference during polling.

    Installation state, runtime inventory and successful inference are separate
    facts. A process restart has no in-memory proof of inference and reports
    not_tested until a real Companion or evaluation request succeeds.
    """
    now = time.monotonic()
    checked_at = float(_READINESS_CACHE.get("checked_at") or 0.0)
    cached = _READINESS_CACHE.get("result")
    if not isinstance(cached, dict):
        return {
            "available": False,
            "state": "not_tested",
            "model": os.getenv("OLLAMA_MODEL", "qwen3.5:4b"),
            "last_attempt_at": None,
            "last_success_at": None,
            "failure_reason": None,
            "source": "actual_inference",
            "stale": False,
        }

    age_seconds = max(0, int(now - checked_at))
    stale = age_seconds > 300
    return {
        **cached,
        "available": bool(cached.get("available")) and not stale,
        "state": "stale" if stale else cached.get("state", "unavailable"),
        "stale": stale,
        "age_seconds": age_seconds,
    }

def qwen_runtime_status() -> dict:
    """Return machine-readable installation state and current model inventory status."""
    installation = qwen_installation_status()
    try:
        base, model, timeout = get_ollama_config()
        request = urllib.request.Request(base + "/api/tags", headers={"Accept": "application/json"}, method="GET")
        with urllib.request.urlopen(request, timeout=min(timeout, 10.0)) as response:
            payload = json.loads(response.read().decode("utf-8"))
        models = payload.get("models")
        if not isinstance(models, list):
            return {"available": False, "installation": installation, "error_category": "invalid_model_inventory", "failure_reason": "ollama_tags_missing_models"}
        names = [str(item.get("name", "")) for item in models if isinstance(item, dict)]
        if model not in names:
            return {"available": False, "installation": installation, "model": model, "error_category": "model_not_installed", "failure_reason": "configured_model_missing"}
        return {"available": True, "installation": installation, "model": model, "provider": "local_ollama", "inference_api": "ollama_native_http", "hosted_inference_api": False, "runtime": "bundled_railway_sidecar" if os.getenv("OLLAMA_SIDECAR_ENABLED", "").casefold() == "true" else "private_railway_service"}
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        return {"available": False, "installation": installation, "model": os.getenv("OLLAMA_MODEL", "qwen3.5:4b"), "error_category": detail.get("code", "configuration_unavailable"), "failure_reason": detail.get("reason", "ollama_configuration_invalid")}
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        return {"available": False, "installation": installation, "model": os.getenv("OLLAMA_MODEL", "qwen3.5:4b"), "error_category": "runtime_unreachable", "failure_reason": type(exc).__name__}
