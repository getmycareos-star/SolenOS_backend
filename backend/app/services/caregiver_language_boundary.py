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
            r"\b(?:system prompt|hidden reasoning|api key|provider configuration|"
            r"ollama|qwen(?: model)?|companion|fastapi|sqlalchemy|alembic|tika|tesseract|"
            r"backend|frontend|endpoint|payload|schema|api|json|ocr|"
            r"database|postgres(?:ql)?|sqlite|telemetry|inference pipeline|"
            r"retrieval pipeline|extraction pipeline|migration|idempotent)\b",
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
            r"session token|bearer token|confidence scores?|hash(?:es)?|audit trail|"
            r"diagnostic (?:traces?|logs?|payloads?)|machine-readable metadata)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "opaque_identifier",
        re.compile(
            r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b"
            r"|\bsha-?256\b|\bhttp\s*\d{3}\b"
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
