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

    # Calendar-year language must become an explicit temporal filter. Without
    # this branch, "last year" loses both tokens as stopwords and silently
    # falls back to the bounded recent-history projection.
    # "Past year" is a rolling twelve-month window, not the previous
    # calendar year. Treating it as a calendar year drops recent months and
    # includes months older than the caregiver requested.
    numbered_years = re.search(
        r"\b(?:last|past)\s+(\d{1,6}|one|two|three|four|five|six|seven|eight|nine|ten|"
        r"eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty)\s+years?\b",
        text,
    )
    if numbered_years:
        year_count = numbered_years.group(1)
        years = int(year_count) if year_count.isdigit() else {
            "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
            "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
            "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
            "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
            "nineteen": 19, "twenty": 20,
        }[year_count]
        if years < 1 or years > 20:
            raise ValueError("A numbered care-history year range must be between one and twenty years.")
        start_year = today.year - years
        start_day = min(today.day, calendar.monthrange(start_year, today.month)[1])
        rolling_start = date(start_year, today.month, start_day)
        return {
            "start": datetime.combine(rolling_start, datetime.min.time(), tzinfo=local_timezone).astimezone(UTC).isoformat(),
            "end": datetime.combine(today, datetime.max.time(), tzinfo=local_timezone).astimezone(UTC).isoformat(),
            "timezone": str(local_timezone),
            "basis": f"explicit_rolling_past_{years}_years",
        }

    if re.search(r"\bpast\s+year\b", text):
        start_year = today.year - 1
        start_day = min(today.day, calendar.monthrange(start_year, today.month)[1])
        rolling_start = date(start_year, today.month, start_day)
        return {
            "start": datetime.combine(rolling_start, datetime.min.time(), tzinfo=local_timezone).astimezone(UTC).isoformat(),
            "end": datetime.combine(today, datetime.max.time(), tzinfo=local_timezone).astimezone(UTC).isoformat(),
            "timezone": str(local_timezone),
            "basis": "explicit_rolling_past_year",
        }

    if re.search(r"\b(?:last|previous)\s+year\b", text):
        previous_year = today.year - 1
        return {
            **{
                "start": datetime.combine(date(previous_year, 1, 1), datetime.min.time(), tzinfo=local_timezone).astimezone(UTC).isoformat(),
                "end": datetime.combine(date(previous_year, 12, 31), datetime.max.time(), tzinfo=local_timezone).astimezone(UTC).isoformat(),
                "timezone": str(local_timezone),
                "basis": "explicit_previous_calendar_year",
            }
        }
    if re.search(r"\bthis year\b", text):
        return {
            "start": datetime.combine(date(today.year, 1, 1), datetime.min.time(), tzinfo=local_timezone).astimezone(UTC).isoformat(),
            "end": datetime.combine(today, datetime.max.time(), tzinfo=local_timezone).astimezone(UTC).isoformat(),
            "timezone": str(local_timezone),
            "basis": "explicit_current_calendar_year",
        }

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

    month_lookup = {
        calendar.month_name[index].casefold(): index
        for index in range(1, 13)
    }
    month_lookup.update({
        calendar.month_abbr[index].casefold(): index
        for index in range(1, 13)
    })
    month_lookup["sept"] = 9
    month_pattern = "|".join(
        re.escape(name)
        for name in sorted(month_lookup, key=len, reverse=True)
    )
    full_month_date = re.search(
        rf"\b(?:({month_pattern})\s+(\d{{1,2}})(?:st|nd|rd|th)?[,]?\s+(20\d{{2}})"
        rf"|(\d{{1,2}})(?:st|nd|rd|th)?\s+({month_pattern})\s+(20\d{{2}}))\b",
        text,
    )
    if full_month_date:
        if full_month_date.group(1):
            month_number = month_lookup[full_month_date.group(1).rstrip(".").casefold()]
            day_number = int(full_month_date.group(2))
            year_number = int(full_month_date.group(3))
        else:
            day_number = int(full_month_date.group(4))
            month_number = month_lookup[full_month_date.group(5).rstrip(".").casefold()]
            year_number = int(full_month_date.group(6))
        try:
            selected = date(year_number, month_number, day_number)
        except ValueError as exc:
            raise ValueError("Invalid calendar date in care-history question.") from exc
        return date_window(selected, selected)

    month_year = re.search(
        rf"\b({month_pattern})\s+(20\d{{2}})\b|\b(20\d{{2}})\s+({month_pattern})\b",
        text,
    )
    if month_year:
        if month_year.group(1):
            month_number = month_lookup[month_year.group(1).rstrip(".").casefold()]
            year_number = int(month_year.group(2))
        else:
            year_number = int(month_year.group(3))
            month_number = month_lookup[month_year.group(4).rstrip(".").casefold()]
        month_start = date(year_number, month_number, 1)
        month_end = date(year_number, month_number, calendar.monthrange(year_number, month_number)[1])
        return date_window(month_start, month_end)

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

    # A year mentioned as a time range is not a lexical search term. Treat
    # "in 2025" / "during 2025" as the complete calendar year so old records
    # are not lost behind the recent-history limit.
    year_match = re.search(r"\b(?:in|during|for)\s+(20\d{2})\b", text)
    if year_match:
        selected_year = int(year_match.group(1))
        return date_window(date(selected_year, 1, 1), date(selected_year, 12, 31))

    if re.search(r"\byesterday\b", text):
        start = end = today - timedelta(days=1)
    elif re.search(r"\btoday\b", text):
        start = end = today
    elif re.search(
        r"\b(?:last|past)\s+(one|two|three|four|five|six|seven|eight|nine|ten|"
        r"eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty)\s+(days?|weeks?|months?)\b",
        text,
    ):
        written_match = re.search(
            r"\b(?:last|past)\s+(one|two|three|four|five|six|seven|eight|nine|ten|"
            r"eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty)\s+(days?|weeks?|months?)\b",
            text,
        )
        written_count = {
            "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
            "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
            "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
            "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
            "nineteen": 19, "twenty": 20,
        }[written_match.group(1)]
        written_unit = written_match.group(2)
        if written_unit.startswith("day"):
            start, end = today - timedelta(days=written_count - 1), today
        elif written_unit.startswith("week"):
            start, end = today - timedelta(days=written_count * 7 - 1), today
        else:
            month_index = today.year * 12 + (today.month - 1) - written_count
            start_year, start_month_zero = divmod(month_index, 12)
            start_month = start_month_zero + 1
            start_day = min(today.day, calendar.monthrange(start_year, start_month)[1])
            start, end = date(start_year, start_month, start_day), today
    elif re.search(r"\b(?:last|past)\s+(\d{1,6})\s+days?\b", text):
        days = int(re.search(r"\b(?:last|past)\s+(\d{1,6})\s+days?\b", text).group(1))
        if days < 1 or days > 7300:
            raise ValueError("A care-history day range must be between one day and twenty years.")
        start, end = today - timedelta(days=days - 1), today
    elif re.search(r"\b(?:last|past)\s+(\d{1,6})\s+weeks?\b", text):
        weeks = int(re.search(r"\b(?:last|past)\s+(\d{1,6})\s+weeks?\b", text).group(1))
        if weeks < 1 or weeks > 1040:
            raise ValueError("A care-history week range must be between one week and twenty years.")
        start, end = today - timedelta(days=weeks * 7 - 1), today
    elif re.search(r"\b(?:last|past)\s+(\d{1,6})\s+months?\b", text):
        months = int(re.search(r"\b(?:last|past)\s+(\d{1,6})\s+months?\b", text).group(1))
        if months < 1 or months > 240:
            raise ValueError("A care-history month range must be between one month and twenty years.")
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
    if temporal_window:
        # Calendar-year and ISO-date tokens belong to the temporal filter, not
        # to the message text or lexical ranking.
        direct_terms = [term for term in direct_terms if not re.fullmatch(r"20\d{2}(?:-\d{2}-\d{2})?", term)]
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
    ordered_statement = statement.order_by(
        ConversationMessage.created_at.desc(), ConversationMessage.id.desc()
    )
    # A lexical match can occur in an old conversation. Do not cap the
    # candidate window at a handful of recent messages: scan every authorized
    # match and retain only the best bounded results. Unfiltered reads remain
    # recent-first and bounded to avoid loading an entire chat archive.
    rows = (
        ordered_statement.execution_options(stream_results=True).yield_per(200)
        if terms or temporal_window
        else ordered_statement.limit(bounded_limit + 1).all()
    )
    expanded_only = [term for term in terms if term not in direct_terms]
    ranked_items: list[tuple[int, str, str, dict[str, Any]]] = []
    candidate_count = 0
    for message, conversation in rows:
        candidate_count += 1
        item = {
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
            "source_attribution": (
                "caregiver_companion_message"
                if str(message.role or "").casefold() == "user"
                else "solenos_companion_response"
                if str(message.role or "").casefold() == "assistant"
                else "unknown_conversation_actor"
            ),
        }
        score = (
            _retrieval_score(item, query or "", direct_terms, expanded_only)
            if direct_terms else 0
        )
        ranked_items.append((score, item.get("recorded_at") or "", item["id"], item))
        if len(ranked_items) > bounded_limit:
            ranked_items.sort(key=lambda entry: (entry[0], entry[1], entry[2]), reverse=True)
            ranked_items.pop()
    items = [entry[3] for entry in ranked_items]
    items.sort(key=lambda item: (item.get("recorded_at") or "", item["id"]))
    return {
        "status": "available" if items else "empty",
        "table": "conversation_messages",
        "is_evidence": False,
        "is_lcr_assertion": False,
        "is_care_evidence": False,
        "items": items,
        "returned_count": len(items),
        "candidate_count": candidate_count,
        "candidate_scan_complete": bool(terms or temporal_window),
        "has_more": candidate_count > len(items),
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
        "retrieval_strategy": context.get("retrieval_strategy") or ("person_scoped_full_source_scan" if include_all_history else "hybrid_lexical_semantic_temporal"),
        "temporal_window": temporal_window,
    }
    return context
