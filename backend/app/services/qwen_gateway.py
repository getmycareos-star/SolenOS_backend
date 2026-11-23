"""SolenOS-owned Qwen gateway.

The model is a support capability, never an authority. This gateway only talks to
Ollama's native HTTP API; it never calls a hosted inference provider.
"""
from __future__ import annotations

import http.client
import json
import re
import logging
import os
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

from fastapi import HTTPException

logger = logging.getLogger(__name__)
_READINESS_CACHE: dict[str, object] = {"checked_at": 0.0, "result": None}
_INFERENCE_VERIFICATION_CACHE: dict[str, object] = {"verified_at": 0.0, "model": None}


def _mark_inference_verified(model: str) -> None:
    _INFERENCE_VERIFICATION_CACHE.update({"verified_at": time.monotonic(), "model": model})
    _READINESS_CACHE.update({"checked_at": 0.0, "result": None})


def _mark_inference_unverified(model: str) -> None:
    if _INFERENCE_VERIFICATION_CACHE.get("model") == model:
        _INFERENCE_VERIFICATION_CACHE.update({"verified_at": 0.0, "model": None})
    _READINESS_CACHE.update({"checked_at": 0.0, "result": None})


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
    if not model.casefold().startswith("qwen"):
        raise HTTPException(
            status_code=503,
            detail={
                "code": "unsupported_model",
                "reason": "qwen_model_required",
                "message": "SolenOS requires a Qwen model; no inference was attempted.",
            },
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
        model = os.getenv("OLLAMA_MODEL", "qwen3.5:4b")
        _mark_inference_unverified(model)
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        _log_inference_result(model, started, timeout or 0.0, "configuration_unavailable", detail.get("code", "ollama_configuration_invalid"))
        raise
    # Keep the model context bounded, but large enough for the authorized
    # source-grounded care context. A 1,024-token window silently truncates
    # multi-record prompts (often to their instructions), causing grounded
    # Companion answers to fail even when retrieval succeeded. Deployments
    # with tighter memory limits can explicitly lower OLLAMA_NUM_CTX.
    try:
        num_ctx = max(1024, min(int(os.getenv("OLLAMA_NUM_CTX", "4096")), 8192))
    except (TypeError, ValueError):
        num_ctx = 4096
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
    # Callers may tune generation quality, but must never bypass the gateway's
    # memory/CPU ceilings or inject arbitrary Ollama runtime options. In
    # particular, an unbounded num_ctx override can exceed Railway's memory
    # budget and restart the API during a caregiver request.
    if options:
        allowed_quality_options = {"temperature", "top_p", "top_k", "repeat_penalty"}
        for key in allowed_quality_options:
            if key not in options:
                continue
            try:
                value = float(options[key])
            except (TypeError, ValueError):
                continue
            if key == "temperature":
                bounded_options[key] = max(0.0, min(value, 2.0))
            elif key == "top_p":
                bounded_options[key] = max(0.0, min(value, 1.0))
            elif key == "top_k":
                bounded_options[key] = max(1, min(int(value), 100))
            elif key == "repeat_penalty":
                bounded_options[key] = max(0.5, min(value, 2.0))
        for key, lower, upper in (
            # Per-call overrides may reduce resource use, never raise the
            # deployment's configured context or CPU-thread ceiling.
            ("num_ctx", 1024, num_ctx),
            ("num_predict", 64, 1024),
            ("num_thread", 1, num_thread),
        ):
            if key not in options:
                continue
            try:
                bounded_options[key] = max(lower, min(int(options[key]), upper))
            except (TypeError, ValueError):
                continue
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
    # Ollama can expose /api/tags before its lazily-started llama-server has
    # finished loading the model. During that narrow cold-start window, the
    # local API may reset or refuse the first connection. Retry only those
    # connection-establishment failures; do not retry model HTTP errors or
    # inference timeouts, and never mark a failed attempt as a saved care fact.
    try:
        retry_seconds = max(0.0, min(float(os.getenv("OLLAMA_STARTUP_RETRY_SECONDS", str(timeout or configured_timeout))), 180.0))
    except (TypeError, ValueError):
        retry_seconds = 45.0
    retry_deadline = None
    last_startup_error = None
    first_attempt = True
    transient_http_retry_used = False
    try:
        while True:
            if first_attempt:
                attempt_timeout = timeout or configured_timeout
            else:
                remaining = retry_deadline - time.monotonic()
                if remaining <= 0:
                    raise last_startup_error
                # urlopen's socket timeout applies to each request, not to the
                # retry loop as a whole. Cap each retry by the remaining startup
                # budget so a final connection attempt cannot overrun it.
                attempt_timeout = min(timeout or configured_timeout, remaining)
            try:
                with urllib.request.urlopen(request, timeout=attempt_timeout) as response:
                    result = json.loads(response.read().decode("utf-8"))
                break
            except urllib.error.HTTPError as exc:
                # A short, single retry covers transient Ollama startup/overload
                # responses without creating an unbounded inference retry loop.
                # Missing models and request errors remain immediate failures.
                if exc.code in {429, 500, 502, 503, 504} and not transient_http_retry_used:
                    transient_http_retry_used = True
                    retry_after = None
                    try:
                        retry_after = float(exc.headers.get("Retry-After", "0.5"))
                    except (AttributeError, TypeError, ValueError):
                        retry_after = 0.5
                    retry_delay = max(0.0, min(retry_after, 2.0))
                    exc.close()
                    if retry_delay:
                        time.sleep(retry_delay)
                    continue
                raise
            except urllib.error.URLError as exc:
                reason = getattr(exc, "reason", exc)
                retryable_startup_failure = isinstance(
                    reason,
                    (
                        ConnectionRefusedError,
                        ConnectionResetError,
                        ConnectionAbortedError,
                        BrokenPipeError,
                        http.client.RemoteDisconnected,
                    ),
                )
                if first_attempt:
                    retry_deadline = time.monotonic() + retry_seconds
                remaining = retry_deadline - time.monotonic()
                if not retryable_startup_failure or remaining <= 0:
                    raise
                last_startup_error = exc
                first_attempt = False
                logger.info("Local Qwen runtime is still starting; retrying inference connection")
                time.sleep(min(1.0, remaining))
            except (
                ConnectionRefusedError,
                ConnectionResetError,
                ConnectionAbortedError,
                BrokenPipeError,
                http.client.RemoteDisconnected,
            ) as exc:
                # urllib may surface a connection-establishment failure
                # directly instead of wrapping it in URLError. Treat both
                # forms identically during the bounded model cold-start window.
                if first_attempt:
                    retry_deadline = time.monotonic() + retry_seconds
                remaining = retry_deadline - time.monotonic()
                if remaining <= 0:
                    raise
                last_startup_error = exc
                first_attempt = False
                logger.info("Local Qwen runtime is still starting; retrying inference connection")
                time.sleep(min(1.0, remaining))
    except urllib.error.HTTPError as exc:
        _mark_inference_unverified(model)
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
    except (
        ConnectionRefusedError,
        ConnectionResetError,
        ConnectionAbortedError,
        BrokenPipeError,
        http.client.RemoteDisconnected,
    ) as exc:
        _mark_inference_unverified(model)
        failure_category, failure_reason = "runtime_unreachable", type(exc).__name__
        _log_inference_result(model, started, timeout or configured_timeout, failure_category, failure_reason)
        raise HTTPException(status_code=503, detail={"code": failure_category, "reason": failure_reason, "message": "SolenOS AI assistance is unavailable."}) from exc
    except http.client.HTTPException as exc:
        # A truncated or malformed HTTP message is an upstream protocol
        # failure, not an uncaught server error and never a successful answer.
        _mark_inference_unverified(model)
        failure_category, failure_reason = "invalid_upstream_response", type(exc).__name__
        _log_inference_result(model, started, timeout or configured_timeout, failure_category, failure_reason)
        raise HTTPException(status_code=502, detail={"code": failure_category, "reason": failure_reason, "message": "SolenOS AI assistance is unavailable."}) from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        _mark_inference_unverified(model)
        failure_category, failure_reason = "invalid_upstream_response", type(exc).__name__
        _log_inference_result(model, started, timeout or configured_timeout, failure_category, failure_reason)
        raise HTTPException(status_code=502, detail={"code": failure_category, "reason": failure_reason, "message": "SolenOS AI assistance is unavailable."}) from exc
    except TimeoutError as exc:
        _mark_inference_unverified(model)
        failure_category, failure_reason = "inference_timeout", "socket_timeout"
        _log_inference_result(model, started, timeout or configured_timeout, failure_category, failure_reason)
        raise HTTPException(status_code=504, detail={"code": failure_category, "reason": failure_reason, "message": "SolenOS AI assistance is unavailable."}) from exc
    except urllib.error.URLError as exc:
        _mark_inference_unverified(model)
        if isinstance(getattr(exc, "reason", None), TimeoutError):
            failure_category, failure_reason = "inference_timeout", "url_timeout"
            _log_inference_result(model, started, timeout or configured_timeout, failure_category, failure_reason)
            raise HTTPException(status_code=504, detail={"code": failure_category, "reason": failure_reason, "message": "SolenOS AI assistance is unavailable."}) from exc
        failure_category, failure_reason = "runtime_unreachable", type(getattr(exc, "reason", exc)).__name__
        _log_inference_result(model, started, timeout or configured_timeout, failure_category, failure_reason)
        raise HTTPException(status_code=503, detail={"code": failure_category, "reason": failure_reason, "message": "SolenOS AI assistance is unavailable."}) from exc
    except (ValueError, OSError) as exc:
        _mark_inference_unverified(model)
        failure_category, failure_reason = "runtime_error", type(exc).__name__
        _log_inference_result(model, started, timeout or configured_timeout, failure_category, failure_reason)
        raise HTTPException(status_code=503, detail={"code": failure_category, "reason": failure_reason, "message": "SolenOS AI assistance is unavailable."}) from exc
    # Treat a syntactically valid JSON response with an unexpected shape as
    # an upstream protocol failure. Calling .get() on a list, null, or scalar
    # here would otherwise escape as an unhandled AttributeError and surface
    # as a generic 500 to the caregiver.
    message = result.get("message") if isinstance(result, dict) else None
    if not isinstance(message, dict):
        _mark_inference_unverified(model)
        failure_category, failure_reason = "invalid_upstream_response", "unexpected_response_shape"
        _log_inference_result(model, started, timeout or configured_timeout, failure_category, failure_reason)
        raise HTTPException(status_code=502, detail={"code": failure_category, "reason": failure_reason, "message": "SolenOS AI assistance is unavailable."})
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        _mark_inference_unverified(model)
        failure_category, failure_reason = "empty_model_response", "missing_message_content"
        _log_inference_result(model, started, timeout or configured_timeout, failure_category, failure_reason)
        raise HTTPException(status_code=502, detail={"code": failure_category, "reason": failure_reason, "message": "SolenOS AI assistance is unavailable."})
    _mark_inference_verified(model)
    _log_inference_result(model, started, timeout or configured_timeout, None, None)
    return content.strip()



def _expand_quote_to_source_sentence(source: str, quote: str) -> str:
    """Expand a model-selected substring to its exact containing source sentence."""
    if not quote or source.count(quote) != 1:
        return quote
    for match in re.finditer(r"[^.!?]+(?:[.!?]|$)", source, flags=re.DOTALL):
        sentence = match.group(0).strip()
        if quote not in sentence:
            continue
        sentence = re.sub(r"^\[Page\s+\d+\]\s*", "", sentence).strip()
        if quote in sentence and len(sentence) <= 1000:
            return sentence
        break
    return quote


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
                "You extract explicitly stated care-relevant statements from source documents, which may be clinician-authored, "
                "caregiver-authored, or patient-reported. You are not the authority: every output is only a source claim "
                "pending caregiver review. "
                "Return valid JSON only with exactly this shape: "
                '{"observations":[{"subject":"string","observation":"string","source_quote":"exact verbatim substring from source"}],'
                '"open_questions":["string"]}. '
                "Only include statements explicitly supported by the source. Do not infer a diagnosis, cause, risk, or "
                "medication change. However, do extract a diagnosis, medication discontinuation, dosage transition, or "
                "current-versus-discharge discrepancy when the source explicitly states it. Preserve the exact wording, "
                "dose values, and historical/current qualifiers as source claims requiring caregiver review. Never turn "
                "these statements into a clinical conclusion, recommendation, prescription, or active medication instruction. "
                "Do not omit an explicit source statement solely because it mentions a diagnosis or medication. "
                "Treat mobility, walking, transfers, eating, drinking, appetite, sleep, mood, behaviour, pain, continence, "
                "communication, daily activities, medication, diagnoses, allergies, appointments, care instructions, and "
                "caregiver or patient observations as care-relevant. Include a plain factual statement from these domains "
                "even when it is neutral, positive, or not framed as a problem; do not require a risk, abnormality, change, "
                "or repeated observation. Do not rank statements by clinical importance or omit a simple observation "
                "because another statement seems more important. Return separate candidates for distinct observations. "
                "Every source_quote must be copied exactly from the source, and the observation and subject must themselves "
                "be verbatim substrings of that source_quote. Do not paraphrase, summarize, normalize, or add details. "
                "For each source_quote, retain the complete local clause or sentence needed to preserve the observation's "
                "meaning, including who reported or observed it, nearby time wording, negation, and uncertainty or hedge words. "
                "Never remove attribution phrases such as 'the caregiver reported', 'the patient denies', or 'the nurse observed'. "
                "Do not clip away qualifiers such as 'previously', 'discontinued', 'currently', 'seemed', 'I think', 'yesterday', or "
                "'last night', and do not join separate clauses when they describe distinct observations. "
                "Never merge statements from different people. If the source clearly contains multiple patient identities, "
                "return no observations and ask the caregiver to separate the records before review. "
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
        quote = _expand_quote_to_source_sentence(bounded_source, quote)
        if observation.strip() not in quote or subject.strip().casefold() not in quote.casefold():
            continue
        # Preserve explicit source statements (including diagnoses and stated causes)
        # as review-only candidates. The exact-quote and substring checks above are
        # the authority boundary; lexical suppression would erase documented history.
        if any(term in observation.casefold() for term in ("should increase dose", "should stop medication")):
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
    """Report installation and runtime inventory only; never run inference in health polling.

    A successful /api/tags response proves the configured model is installed and
    the Ollama runtime is reachable. It does not prove that inference succeeds.
    Actual Companion requests and explicit evaluation workflows are the only
    inference evidence.
    """
    now = time.monotonic()
    cached_at = float(_READINESS_CACHE.get("checked_at") or 0.0)
    cached = _READINESS_CACHE.get("result")
    if not force and cached and now - cached_at < 15.0:
        return dict(cached)

    installation = qwen_installation_status()
    try:
        base, model, timeout = get_ollama_config()
        request = urllib.request.Request(
            base + "/api/tags",
            headers={"Accept": "application/json"},
            method="GET",
        )
        with urllib.request.urlopen(request, timeout=min(timeout, 5.0)) as response:
            inventory = json.loads(response.read().decode("utf-8"))
        # A successful HTTP response is not a valid Ollama inventory unless
        # its top-level JSON value is an object. Treat null, arrays and scalars
        # as an unavailable runtime instead of leaking AttributeError as HTTP 500.
        if not isinstance(inventory, dict):
            inventory = {}
        models = inventory.get("models")
        if not isinstance(models, list):
            result = {
                "available": False,
                "state": "runtime_unavailable",
                "model": model,
                "installation": installation,
                "model_installed": False,
                "runtime_available": True,
                "inference_verified": False,
                "inference_status": "not_verified",
                "failure_reason": "invalid_model_inventory",
            }
        else:
            names = [str(item.get("name", "")) for item in models if isinstance(item, dict)]
            installed = model in names
            if not installed:
                result = {
                    "available": False,
                    "state": "model_not_installed",
                    "model": model,
                    "installation": installation,
                    "model_installed": False,
                    "runtime_available": True,
                    "inference_verified": False,
                    "inference_status": "not_verified",
                    "failure_reason": "configured_model_missing",
                }
            else:
                verified_at = float(_INFERENCE_VERIFICATION_CACHE.get("verified_at") or 0.0)
                inference_verified = (
                    _INFERENCE_VERIFICATION_CACHE.get("model") == model
                    and verified_at > 0
                    and now - verified_at <= 120.0
                )
                result = {
                    # Model inventory is not proof that the model can generate.
                    "available": inference_verified,
                    "state": "operational" if inference_verified else "runtime_available",
                    "model": model,
                    "installation": installation,
                    "model_installed": True,
                    "runtime_available": True,
                    "inference_verified": inference_verified,
                    "inference_status": "verified" if inference_verified else "not_verified",
                    "failure_reason": None,
                }
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {"reason": "configuration_unavailable"}
        result = {
            "available": False,
            "state": "runtime_unavailable",
            "model": os.getenv("OLLAMA_MODEL", "qwen3.5:4b"),
            "installation": installation,
            "model_installed": False,
            "runtime_available": False,
            "inference_verified": False,
            "inference_status": "not_verified",
            "failure_reason": detail.get("reason", "ollama_configuration_invalid"),
            "error_category": detail.get("code", "configuration_unavailable"),
        }
    except http.client.HTTPException as exc:
        result = {
            "available": False,
            "state": "runtime_unavailable",
            "model": os.getenv("OLLAMA_MODEL", "qwen3.5:4b"),
            "installation": installation,
            "model_installed": False,
            "runtime_available": False,
            "inference_verified": False,
            "inference_status": "not_verified",
            "failure_reason": type(exc).__name__,
            "error_category": "invalid_model_inventory",
        }
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        result = {
            "available": False,
            "state": "runtime_unavailable",
            "model": os.getenv("OLLAMA_MODEL", "qwen3.5:4b"),
            "installation": installation,
            "model_installed": False,
            "runtime_available": False,
            "inference_verified": False,
            "inference_status": "not_verified",
            "failure_reason": type(exc).__name__,
            "error_category": "runtime_unreachable",
        }
    _READINESS_CACHE.update({"checked_at": now, "result": result})
    return dict(result)


def qwen_runtime_status() -> dict:
    """Return machine-readable installation state and current model inventory status."""
    installation = qwen_installation_status()
    try:
        base, model, timeout = get_ollama_config()
        request = urllib.request.Request(base + "/api/tags", headers={"Accept": "application/json"}, method="GET")
        with urllib.request.urlopen(request, timeout=min(timeout, 10.0)) as response:
            payload = json.loads(response.read().decode("utf-8"))
        # Keep runtime status fail-closed for syntactically valid but malformed
        # top-level JSON responses from the Ollama service.
        if not isinstance(payload, dict):
            payload = {}
        models = payload.get("models")
        if not isinstance(models, list):
            return {
                "available": False,
                "runtime_available": True,
                "model_installed": False,
                "inference_verified": False,
                "inference_status": "not_verified",
                "installation": installation,
                "error_category": "invalid_model_inventory",
                "failure_reason": "ollama_tags_missing_models",
            }
        names = [str(item.get("name", "")) for item in models if isinstance(item, dict)]
        if model not in names:
            return {
                "available": False,
                "runtime_available": True,
                "model_installed": False,
                "inference_verified": False,
                "inference_status": "not_verified",
                "installation": installation,
                "model": model,
                "error_category": "model_not_installed",
                "failure_reason": "configured_model_missing",
            }
        verified_at = float(_INFERENCE_VERIFICATION_CACHE.get("verified_at") or 0.0)
        inference_verified = (
            _INFERENCE_VERIFICATION_CACHE.get("model") == model
            and verified_at > 0
            and time.monotonic() - verified_at <= 120.0
        )
        return {
            "available": inference_verified,
            "runtime_available": True,
            "model_installed": True,
            "inference_verified": inference_verified,
            "inference_status": "verified" if inference_verified else "not_verified",
            "installation": installation,
            "model": model,
            "provider": "local_ollama",
            "inference_api": "ollama_native_http",
            "hosted_inference_api": False,
            "runtime": "bundled_railway_sidecar" if os.getenv("OLLAMA_SIDECAR_ENABLED", "").casefold() == "true" else "private_railway_service",
        }
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        return {
            "available": False,
            "runtime_available": False,
            "model_installed": False,
            "inference_verified": False,
            "inference_status": "not_verified",
            "installation": installation,
            "model": os.getenv("OLLAMA_MODEL", "qwen3.5:4b"),
            "error_category": detail.get("code", "configuration_unavailable"),
            "failure_reason": detail.get("reason", "ollama_configuration_invalid"),
        }
    except http.client.HTTPException as exc:
        return {
            "available": False,
            "runtime_available": False,
            "model_installed": False,
            "inference_verified": False,
            "inference_status": "not_verified",
            "installation": installation,
            "model": os.getenv("OLLAMA_MODEL", "qwen3.5:4b"),
            "error_category": "invalid_model_inventory",
            "failure_reason": type(exc).__name__,
        }
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        return {
            "available": False,
            "runtime_available": False,
            "model_installed": False,
            "inference_verified": False,
            "inference_status": "not_verified",
            "installation": installation,
            "model": os.getenv("OLLAMA_MODEL", "qwen3.5:4b"),
            "error_category": "runtime_unreachable",
            "failure_reason": type(exc).__name__,
        }
