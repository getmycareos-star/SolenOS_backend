"""SolenOS-owned Qwen gateway.

The model is a support capability, never an authority. This gateway only talks to
Ollama's native HTTP API; it never calls a hosted inference provider.
"""
from __future__ import annotations

import json
import re
import os
import urllib.error
import urllib.request
from urllib.parse import urlparse

from fastapi import HTTPException


def get_ollama_config() -> tuple[str, str, float]:
    base = os.getenv("OLLAMA_BASE_URL", "").strip().rstrip("/")
    model = os.getenv("OLLAMA_MODEL", "qwen2.5:0.5b").strip()
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
    base, model, configured_timeout = get_ollama_config()
    # Keep inference memory bounded for Railway deployments. The 1.5B model's
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
    if response_format is not None:
        payload["format"] = response_format
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        base + "/api/chat",
        data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout or configured_timeout) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
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
                raise HTTPException(
                    status_code=503,
                    detail="SolenOS local Qwen model is not installed; no model response was generated.",
                ) from exc
        raise HTTPException(
            status_code=503,
            detail="SolenOS local Qwen is unavailable; no model response was generated.",
        ) from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(
            status_code=502,
            detail="SolenOS local Qwen returned an invalid response; no model response was generated.",
        ) from exc
    except TimeoutError as exc:
        raise HTTPException(
            status_code=504,
            detail="SolenOS local Qwen timed out; no model response was generated.",
        ) from exc
    except urllib.error.URLError as exc:
        if isinstance(getattr(exc, "reason", None), TimeoutError):
            raise HTTPException(
                status_code=504,
                detail="SolenOS local Qwen timed out; no model response was generated.",
            ) from exc
        raise HTTPException(
            status_code=503,
            detail="SolenOS local Qwen is unavailable; no model response was generated.",
        ) from exc
    except (ValueError, OSError) as exc:
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



def _source_sentence_fallback(
    source_text: str,
    *,
    source_event_id: str,
    source_evidence_id: str | None,
    existing_quotes: set[str] | None = None,
) -> list[dict]:
    """Select exact care-relevant source sentences when the small model omits them.

    This is deterministic source selection, not generated clinical interpretation.
    The candidates remain review-only and retain the exact original sentence.
    """
    care_terms = re.compile(
        r"\b(?:patient|person|resident|mother|father|she|he|they|caregiver|nurse|doctor|"
        r"mobility|walk(?:ed|ing)?|fall|fell|sleep|slept|appetite|ate|eating|pain|"
        r"medication|medicine|dose|blood pressure|oxygen|temperature|wound|discharge|"
        r"appointment|diagnosis|symptom|breathing|swelling|confusion|memory|agitation|"
        r"hydration|fluid|urine|bowel|toilet|transfer|support|assistance|dressing|feeding)\b",
        re.IGNORECASE,
    )
    quotes = existing_quotes or set()
    pieces = re.split(r"(?<=[.!?])\s+|\n+|(?<=:)\s+|(?<=;)\s+", source_text)
    candidates = []
    for piece in pieces:
        sentence = piece.strip().strip("•*-–— \t")
        if len(sentence) < 12 or len(sentence) > 500 or sentence in quotes:
            continue
        # A document-introduction fragment such as "The discharge note states:"
        # is attribution scaffolding, not a care assertion. Keep the exact
        # statement following it, but never promote the heading fragment.
        if re.search(r"\b(?:states|reports|notes|records|documents|indicates)\s*:$", sentence, re.IGNORECASE):
            continue
        if not care_terms.search(sentence):
            continue
        candidate = {
            "subject": "Document statement",
            "observation": sentence,
            "source_quote": sentence,
            "review_status": "pending_review",
            "source_event_id": source_event_id,
            "extraction_method": "deterministic_verbatim_source_sentence",
        }
        if source_evidence_id:
            candidate["source_evidence_id"] = source_evidence_id
        candidates.append(candidate)
        if len(candidates) >= 8:
            break
    return candidates


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
                "Extract explicit care statements from the supplied document. "
                "Return JSON only: "
                '{"observations":[{"subject":"short subject","observation":"plain statement","source_quote":"exact copied sentence"}],'
                '"open_questions":["short question"]}. '
                "For every clear care statement, create one candidate. Do not omit a clear statement. "
                "Copy source_quote character-for-character from the document. "
                "Do not change numbers, dates, units, names, or spelling. "
                "Do not add causes, diagnoses, advice, or facts absent from the source. "
                "If the document has no care statements, return an empty observations array. "
                "Return at most 8 observations and 3 questions."
            ),
        },
        {"role": "user", "content": "DOCUMENT TEXT:\n" + bounded_source},
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
            "extraction_method": "qwen_verbatim_source_extraction",
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
    if not observations:
        observations = _source_sentence_fallback(
            bounded_source,
            source_event_id=source_event_id,
            source_evidence_id=source_evidence_id,
        )
    else:
        # Add exact, care-relevant source sentences not already represented by
        # Qwen. This closes omissions without inventing or paraphrasing evidence.
        observations.extend(_source_sentence_fallback(
            bounded_source,
            source_event_id=source_event_id,
            source_evidence_id=source_evidence_id,
            existing_quotes={item["source_quote"] for item in observations},
        ))
        observations = observations[:12]
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
