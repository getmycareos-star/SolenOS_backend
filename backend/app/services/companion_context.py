"""Query-relevant selection for authorized Companion context.

This module ranks already-authorized records; it never changes their meaning,
provenance, access scope, or canonical status. Recent records remain represented
while older records can be retrieved when their content matches the question.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from typing import Callable, Iterable, TypeVar

T = TypeVar("T")
_TOKEN = re.compile(r"[a-z0-9]+", re.IGNORECASE)
_STOP_WORDS = {
    "about", "after", "again", "also", "and", "are", "been", "before",
    "being", "but", "can", "could", "did", "does", "for", "from", "had",
    "has", "have", "her", "here", "him", "his", "how", "into", "its",
    "just", "may", "more", "most", "not", "our", "out", "please", "she",
    "should", "some", "such", "than", "that", "the", "their", "them",
    "then", "there", "these", "they", "this", "those", "through", "was",
    "were", "what", "when", "where", "which", "who", "why", "will",
    "with", "would", "you", "your",
}


def _terms(value: str) -> list[str]:
    return [
        token for token in _TOKEN.findall((value or "").casefold())
        if len(token) >= 3 and token not in _STOP_WORDS
    ]


def rank_authorized_context(
    items: Iterable[T],
    query: str,
    *,
    text_of: Callable[[T], str],
    limit: int,
    always_keep_recent: int = 3,
) -> list[T]:
    """Keep recent anchors and rank the remaining authorized items by query fit.

    Input order must be newest/relevant-first according to the caller's trusted
    database ordering. Selection is deterministic and ties preserve input order.
    Returns selected items in their original order for temporal readability.
    """
    rows = list(items)
    if limit <= 0 or not rows:
        return []
    if len(rows) <= limit:
        return rows

    query_terms = set(_terms(query))
    anchor_count = min(max(always_keep_recent, 0), limit, len(rows))
    if not query_terms:
        return rows[:limit]

    documents = [_terms(text_of(row)) for row in rows]
    document_frequency = Counter({
        term: sum(1 for doc in documents if term in set(doc))
        for term in query_terms
    })
    # A term appearing in many candidate records carries less discriminating value.
    scores: list[tuple[float, int]] = []
    for index, terms in enumerate(documents):
        counts = Counter(terms)
        score = 0.0
        for term in query_terms:
            if counts[term]:
                inverse_frequency = math.log1p(len(rows) / max(document_frequency[term], 1))
                score += inverse_frequency * (1.0 + math.log(counts[term]))
        if len(query_terms) >= 2:
            normalized_text = " ".join(terms)
            normalized_query = " ".join(_terms(query))
            if normalized_query and normalized_query in normalized_text:
                score += 3.0
        scores.append((score, index))

    selected = set(range(anchor_count))
    ranked = sorted(
        (item for item in scores if item[1] not in selected and item[0] > 0),
        key=lambda item: (-item[0], item[1]),
    )
    for _, index in ranked:
        if len(selected) >= limit:
            break
        selected.add(index)
    # Fill remaining slots by recency/order when the question has little overlap.
    for index in range(len(rows)):
        if len(selected) >= limit:
            break
        selected.add(index)
    return [row for index, row in enumerate(rows) if index in selected]
