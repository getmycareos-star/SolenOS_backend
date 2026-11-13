"""Unified, permission-scoped LCR retrieval composition.

The canonical care projection remains owned by :mod:`lcr_context`. This
composition adds bounded prior Companion dialogue as conversational context,
never as evidence or an authoritative care assertion. Every query is scoped by
both person and caregiver through the persisted conversation-session relation.
"""
from __future__ import annotations

import calendar
import re
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.care import Caregiver
from app.models.collaboration import ConversationMessage, ConversationSession
from app.services.lcr_context import (
    _SEARCH_STOPWORDS,
    _expanded_search_terms,
    _retrieval_score,
    build_lcr_context,
)

MAX_CONVERSATION_CONTEXT_ITEMS = 8
MAX_CONVERSATION_MESSAGE_CHARS = 1200
MAX_QUERY_TERMS = 8
_TEMPORAL_STOPWORDS = {"last", "past", "this", "week", "month", "year", "today", "yesterday", "days", "day", "since", "before", "after", "between", "and", "on"}

def _temporal_window(
    query: str | None,
    *,
    now: datetime | None = None,
    timezone_name: str = "UTC",
) -> dict[str, str] | None:
    """Resolve explicit date/range language in the caregiver's local timezone.

    Invalid or reversed explicit dates fail closed instead of silently falling
    back to an unrestricted history search.
    """
    text = (query or "").casefold()
    try:
        local_timezone = ZoneInfo(timezone_name or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        local_timezone = UTC
    current = now or datetime.now(local_timezone)
    if current.tzinfo is None:
        current = current.replace(tzinfo=local_timezone)
    else:
        current = current.astimezone(local_timezone)
    today = current.date()

    def parsed_date(value: str) -> date:
        try:
            return date.fromisoformat(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid calendar date in care-history question: {value}") from exc

    def date_window(start_date: date, end_date: date) -> dict[str, str]:
        if start_date > end_date:
            raise ValueError("The start date is after the end date in the care-history question.")
        return {
            "start": datetime.combine(start_date, datetime.min.time(), tzinfo=local_timezone).astimezone(UTC).isoformat(),
            "end": datetime.combine(end_date, datetime.max.time(), tzinfo=local_timezone).astimezone(UTC).isoformat(),
            "timezone": str(local_timezone),
            "basis": "explicit_user_temporal_expression",
        }

    start = end = None
    range_match = re.search(
        r"\b(?:between\s+(20\d{2}-\d{2}-\d{2})\s+and\s+(20\d{2}-\d{2}-\d{2})|"
        r"from\s+(20\d{2}-\d{2}-\d{2})\s+to\s+(20\d{2}-\d{2}-\d{2}))\b",
        text,
    )
    if range_match:
        values = [value for value in range_match.groups() if value]
        return date_window(parsed_date(values[0]), parsed_date(values[1]))

    date_match = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", text)
    if date_match:
        selected = parsed_date(date_match.group(1))
        if re.search(r"\b(?:on|for)\s+20\d{2}-\d{2}-\d{2}", text):
            start = end = selected
        elif re.search(r"\bsince\s+20\d{2}-\d{2}-\d{2}", text):
            start, end = selected, today
        elif re.search(r"\bafter\s+20\d{2}-\d{2}-\d{2}", text):
            start, end = selected + timedelta(days=1), date(9998, 12, 31)
        elif re.search(r"\bbefore\s+20\d{2}-\d{2}-\d{2}", text):
            if selected <= date(2, 1, 1):
                raise ValueError("The requested date range is outside the supported calendar.")
            start, end = date(2, 1, 1), selected - timedelta(days=1)
        if start is not None:
            return date_window(start, end)

    if re.search(r"\byesterday\b", text):
        start = end = today - timedelta(days=1)
    elif re.search(r"\btoday\b", text):
        start = end = today
    elif re.search(r"\b(?:last|past)\s+(\d{1,3})\s+days?\b", text):
        days = int(re.search(r"\b(?:last|past)\s+(\d{1,3})\s+days?\b", text).group(1))
        if days < 1:
            raise ValueError("A care-history day range must be at least one day.")
        days = min(days, 366)
        start, end = today - timedelta(days=days - 1), today
    elif re.search(r"\b(?:last|past)\s+(\d{1,2})\s+weeks?\b", text):
        weeks = int(re.search(r"\b(?:last|past)\s+(\d{1,2})\s+weeks?\b", text).group(1))
        if weeks < 1:
            raise ValueError("A care-history week range must be at least one week.")
        weeks = min(weeks, 52)
        start, end = today - timedelta(days=weeks * 7 - 1), today
    elif re.search(r"\b(?:last|past)\s+(\d{1,2})\s+months?\b", text):
        months = int(re.search(r"\b(?:last|past)\s+(\d{1,2})\s+months?\b", text).group(1))
        if months < 1:
            raise ValueError("A care-history month range must be at least one month.")
        months = min(months, 12)
        month_index = today.year * 12 + (today.month - 1) - months
        start_year, start_month_zero = divmod(month_index, 12)
        start_month = start_month_zero + 1
        start_day = min(today.day, calendar.monthrange(start_year, start_month)[1])
        start, end = date(start_year, start_month, start_day), today
    elif re.search(r"\blast week\b", text):
        monday = today - timedelta(days=today.weekday())
        start, end = monday - timedelta(days=7), monday - timedelta(days=1)
    elif re.search(r"\bthis week\b", text):
        start, end = today - timedelta(days=today.weekday()), today
    elif re.search(r"\blast month\b", text):
        first_this_month = today.replace(day=1)
        last_month_end = first_this_month - timedelta(days=1)
        start, end = last_month_end.replace(day=1), last_month_end
    elif re.search(r"\bthis month\b", text):
        start, end = today.replace(day=1), today

    if start is None or end is None:
        return None
    return date_window(start, end)


def _conversation_context(
    db: Session,
    *,
    person_id: str,
    caregiver_id: str,
    query: str | None,
    limit: int,
    temporal_window: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Read relevant prior turns; keep all dialogue explicitly non-evidentiary."""
    statement = (
        db.query(ConversationMessage, ConversationSession)
        .join(ConversationSession, ConversationSession.id == ConversationMessage.session_id)
        .filter(
            ConversationSession.person_id == str(person_id),
            ConversationSession.caregiver_id == str(caregiver_id),
        )
    )
    direct_terms = [
        term for term in re.findall(r"[A-Za-z0-9_-]{3,}", (query or "").casefold())
        if term not in _SEARCH_STOPWORDS
    ][:MAX_QUERY_TERMS]
    terms = _expanded_search_terms(direct_terms)
    if temporal_window:
        statement = statement.filter(
            ConversationMessage.created_at >= datetime.fromisoformat(temporal_window["start"]),
            ConversationMessage.created_at <= datetime.fromisoformat(temporal_window["end"]),
        )
    if terms:
        statement = statement.filter(or_(*[
            ConversationMessage.content.ilike(f"%{term}%") for term in terms
        ]))
    bounded_limit = max(1, min(int(limit), MAX_CONVERSATION_CONTEXT_ITEMS))
    candidate_limit = min(max(bounded_limit * 5, bounded_limit + 1), 100) if terms else bounded_limit
    rows = (
        statement.order_by(ConversationMessage.created_at.desc(), ConversationMessage.id.desc())
        .limit(candidate_limit + 1)
        .all()
    )
    items = []
    for message, conversation in rows:
        items.append({
            "id": str(message.id),
            "session_id": str(conversation.id),
            "role": str(message.role or "unknown"),
            "content": (message.content or "")[:MAX_CONVERSATION_MESSAGE_CHARS],
            "event_time": None,
            "recorded_at": message.created_at.isoformat() if message.created_at else None,
            "information_status": "conversation_context_only",
            "interpretation_limit": "Dialogue is not independent care evidence or an accepted LCR assertion.",
            "is_lcr_assertion": False,
            "is_care_evidence": False,
            "source_attribution": "caregiver_companion_message",
        })
    if direct_terms:
        expanded_only = [term for term in terms if term not in direct_terms]
        items.sort(
            key=lambda item: (
                _retrieval_score(item, query or "", direct_terms, expanded_only),
                item.get("recorded_at") or "",
            ),
            reverse=True,
        )
    items = items[:bounded_limit]
    items.sort(key=lambda item: (item.get("recorded_at") or "", item["id"]))
    return {
        "status": "available" if items else "empty",
        "table": "conversation_messages",
        "items": items,
        "returned_count": len(items),
        "has_more": len(rows) > len(items),
        "limit": bounded_limit,
        "interpretation_limit": (
            "Prior Companion dialogue is conversational context only. It is not "
            "a verified care event, accepted LCR assertion, or independent evidence."
        ),
    }


def build_unified_lcr_context(
    db: Session,
    person_id: str,
    caregiver_id: str,
    *,
    limit_per_source: int = 20,
    query: str | None = None,
    conversation_limit: int = MAX_CONVERSATION_CONTEXT_ITEMS,
    include_all_history: bool = False,
    source_offsets: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Compose canonical care sources and authorized prior Companion dialogue.

    Authorization must already have been established by the request's server-side
    session scope. The caregiver/person pair is repeated in the conversation query
    so a caller cannot broaden dialogue retrieval by supplying a session ID.
    """
    if not person_id or not caregiver_id:
        raise ValueError("Authorized person_id and caregiver_id are required")

    caregiver_timezone = (
        db.query(Caregiver.timezone)
        .filter(Caregiver.id == str(caregiver_id), Caregiver.person_id == str(person_id))
        .scalar()
        or "UTC"
    )
    temporal_window = _temporal_window(query, timezone_name=caregiver_timezone)
    context = build_lcr_context(
        db, str(person_id), caregiver_id=str(caregiver_id),
        limit_per_source=limit_per_source, query=query, temporal_window=temporal_window,
        include_all_history=include_all_history, source_offsets=source_offsets,
    )
    conversation = _conversation_context(
        db,
        person_id=str(person_id),
        caregiver_id=str(caregiver_id),
        query=query,
        limit=conversation_limit,
        temporal_window=temporal_window,
    )
    context.setdefault("sources", {})["companion_conversation"] = conversation
    context["source_status"] = {
        name: source.get("status", "unknown")
        for name, source in context["sources"].items()
    }
    context["retrieval_contract"] = {
        "engine": "shared_lcr_context",
        "person_scope": str(person_id),
        "caregiver_scope_applied": True,
        "conversation_is_evidence": False,
        "authoritative_record": "care_assertions_and_canonical_lcr_sources",
        "retrieval_is_bounded": True,
        "full_source_scan_requested": bool(include_all_history),
        "source_offsets": context.get("source_offsets", {}),
        "retrieval_strategy": "person_scoped_full_source_scan" if include_all_history else "hybrid_lexical_semantic_temporal",
        "temporal_window": temporal_window,
    }
    return context
