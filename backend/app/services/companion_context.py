"""Query-aware retrieval helpers for SolenOS Companion context.

This module ranks authorized database rows; it never generates care facts or replies.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Callable, Iterable, TypeVar

T = TypeVar("T")
_TOKEN_RE = re.compile(r"[^\\W_]+", re.UNICODE)
# These words carry little care-topic information. This is retrieval plumbing,
# not a response map or a care-domain conclusion.
_STOP_WORDS = {
    "a", "about", "after", "again", "all", "am", "an", "and", "any", "are",
    "as", "at", "be", "been", "before", "being", "but", "by", "can", "could",
    "did", "do", "does", "for", "from", "had", "has", "have", "he", "her",
    "here", "him", "his", "how", "i", "if", "in", "into", "is", "it", "its",
    "just", "me", "my", "of", "on", "or", "our", "please", "she", "should",
    "so", "some", "that", "the", "their", "them", "there", "they", "this",
    "to", "was", "we", "were", "what", "when", "where", "which", "who", "why",
    "will", "with", "would", "you", "your", "mum", "mom", "mother", "dad",
    "father", "care", "record", "records",
}


def query_terms(query: str) -> set[str]:
    """Return normalized, informative words from the caregiver's current message."""
    return {
        token.casefold()
        for token in _TOKEN_RE.findall(query or "")
        if len(token) > 1 and token.casefold() not in _STOP_WORDS
    }


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def rank_context_rows(
    query: str,
    rows: Iterable[T],
    *,
    text: Callable[[T], str],
    timestamp: Callable[[T], datetime | None],
    limit: int,
    fallback_recent: int = 2,
) -> list[T]:
    """Rank already-authorized rows by query-term overlap, with recent baseline rows.

    Relevance never overrides authorization. Callers must scope database queries
    before passing rows here. Stable source records remain unchanged.
    """
    candidates = list(rows)
    if limit <= 0 or not candidates:
        return []
    terms = query_terms(query)
    now = datetime.now(timezone.utc)
    scored: list[tuple[float, int, T]] = []
    for index, row in enumerate(candidates):
        row_terms = set(_TOKEN_RE.findall(text(row) or ""))
        row_terms = {term.casefold() for term in row_terms}
        overlap = terms & row_terms
        lexical = len(overlap) / max(len(terms), 1)
        observed = _as_utc(timestamp(row))
        age_days = max((now - observed).total_seconds() / 86400, 0) if observed else 3650
        recency = 0.12 / (1 + age_days / 30)
        # Lexical evidence dominates recency; ties retain the database's order.
        scored.append((lexical + recency, index, row))
    scored.sort(key=lambda item: (-item[0], item[1]))
    chosen = [row for _, _, row in scored[:limit]]
    # Include a small recent baseline even when a very old entry is the only
    # lexical match; comparison questions need both topical evidence and context.
    if terms and fallback_recent > 0:
        recent = candidates[:fallback_recent]
        ids = {id(row) for row in chosen}
        for row in recent:
            if id(row) not in ids and len(chosen) < limit:
                chosen.append(row)
                ids.add(id(row))
    return chosen
