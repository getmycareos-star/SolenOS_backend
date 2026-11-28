"""Deterministic detection for internal implementation language in generated caregiver copy.

This is one layer only: the shared model contract supplies communication rules,
Companion grounding/interaction validators check meaning, this module catches
known implementation leakage, and the frontend applies a final display boundary.
It deliberately does not rewrite or filter caregiver-authored source quotations.
"""
from __future__ import annotations

import re

_INTERNAL_LANGUAGE_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "internal_runtime",
        re.compile(
            r"\b(?:system prompt|hidden reasoning|api key|provider configuration|jwt|signature verification|pydantic|"
            r"ollama|qwen(?: model)?|(?:ai|solenos) companion|ask solenos|companion (?:chat|conversation|feature|page)|fastapi|sqlalchemy|alembic|tika|tesseract|"
            r"backend|frontend|endpoint|payload|schema|api|json|ocr|"
            r"database|postgres(?:ql)?|sqlite|telemetry|inference pipeline|"
            r"retrieval pipeline|extraction pipeline|migration|idempotent|traceback|stack trace|internal server error|sqlalchemy error|psycopg|asyncpg|sqlstate|constraint violation|syntax error|connection refused|connection reset|relation does not exist|column does not exist|file not found|permission denied|caregiver_access_grant_id|person_id|exception(?: type)?|attributeerror|typeerror|valueerror|keyerror|runtimeerror|modulenotfounderror)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "internal_record_metadata",
        re.compile(
            r"\b(?:epistemic(?: status| state)?|provenance|reconciliation|"
            r"assertion(?: record| candidate)?|source[_ -]?id|record[_ -]?id|"
            r"evidence[_ -]?id|claim[_ -]?id|request[_ -]?id|"
            r"(?:person|caregiver|document|appointment|handoff|analysis|workflow|event|upload|job|trace|run)[_ -]?id|"
            r"generation[_ -]?status|epistemic[_ -]?(?:state|status)|information[_ -]?status|"
            r"retrieval[_ -]?(?:scan|projection)[_ -]?complete|candidate[_ -]?scan[_ -]?complete|"
            r"missing[_ -]?required[_ -]?sources|selection[_ -]?truncated|response[_ -]?body|"
            r"session token|bearer token|confidence scores?|hash(?:es)?|audit trail|field name|column name|"
            r"diagnostic (?:traces?|logs?|payloads?)|machine-readable metadata)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "opaque_identifier",
        re.compile(
            r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b"
            r"|\bsha-?256\b|\bhttp\s*\d{3}\b"
            r"|\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b"
            r"|\b(?:awaiting|pending|processing|failed|unavailable|partial|rejected|accepted|"
            r"verified|unverified|conflicting|missing|withdrawn|superseded)_[a-z0-9_]+\b",
            re.IGNORECASE,
        ),
    ),
)


def caregiver_language_violations(text: str) -> tuple[str, ...]:
    """Return stable rule codes for known internal language in generated copy."""
    if not isinstance(text, str) or not text.strip():
        return ("empty_response",)
    return tuple(
        rule_code
        for rule_code, pattern in _INTERNAL_LANGUAGE_RULES
        if pattern.search(text)
    )


def contains_internal_caregiver_language(text: str) -> bool:
    """Whether generated caregiver-facing text contains known internal language."""
    return bool(caregiver_language_violations(text))


def _contains_internal_detail(value: object) -> bool:
    if isinstance(value, str):
        return contains_internal_caregiver_language(value)
    if isinstance(value, dict):
        return any(_contains_internal_detail(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_internal_detail(item) for item in value)
    return False


def caregiver_safe_api_error_detail(status_code: int, detail: object) -> object:
    """Keep useful human validation copy; replace internal or opaque error detail."""
    if isinstance(detail, dict) and detail.get("code") == "deletion_request_already_open":
        request = detail.get("request")
        request_id = request.get("id") if isinstance(request, dict) else None
        safe_detail = {
            "code": "deletion_request_already_open",
            "message": "You already have an open deletion request. Check its status before submitting another.",
        }
        if isinstance(request_id, str) and request_id:
            # Preserve only the opaque reference needed to find the existing
            # request; do not return the full privacy-request payload.
            safe_detail["request"] = {"id": request_id}
        return safe_detail
    if not _contains_internal_detail(detail):
        return detail
    if status_code == 401:
        return "Your session may have expired. Please sign in again."
    if status_code == 403:
        return "You don’t have access to that information."
    if status_code == 404:
        return "We couldn’t find that information."
    if status_code == 413:
        return "The files are too large. Choose fewer or smaller files and try again."
    if status_code == 422:
        return "We couldn’t process that information. Please check it and try again."
    return "We couldn’t complete that request. Please try again."
