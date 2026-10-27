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
    """Keep recent anchors, then return query-relevant authorized records.

    Input order must be newest-first according to the caller's trusted database
    ordering. Selection is deterministic; recent anchors come first, followed by
    older matches in relevance order, then remaining rows in original order.
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

    selected_order = list(range(anchor_count))
    selected = set(selected_order)
    ranked = sorted(
        (item for item in scores if item[1] not in selected and item[0] > 0),
        key=lambda item: (-item[0], item[1]),
    )
    for _, index in ranked:
        if len(selected_order) >= limit:
            break
        selected.add(index)
        selected_order.append(index)
    # Fill remaining slots by recency/order when the question has little overlap.
    for index in range(len(rows)):
        if len(selected_order) >= limit:
            break
        if index not in selected:
            selected.add(index)
            selected_order.append(index)
    return [rows[index] for index in selected_order]


def select_context_within_budget(
    groups: dict[str, list[T]],
    *,
    cost_of: Callable[[str, T], int],
    max_chars: int,
) -> dict[str, list[T]]:
    """Select complete records across context groups within a strict char budget.

    Round-robin selection prevents one high-volume domain from crowding out all
    others. Items are never truncated or rewritten; over-budget items are skipped.
    Candidate lists should already be relevance-ranked by the caller.
    """
    selected = {name: [] for name in groups}
    positions = {name: 0 for name in groups}
    remaining = max(max_chars, 0)
    progress = True
    while progress and remaining > 0:
        progress = False
        for name, items in groups.items():
            index = positions[name]
            if index >= len(items):
                continue
            positions[name] += 1
            progress = True
            item = items[index]
            cost = max(int(cost_of(name, item)), 1) + 2
            if cost <= remaining:
                selected[name].append(item)
                remaining -= cost
    return {name: items for name, items in selected.items() if items}
