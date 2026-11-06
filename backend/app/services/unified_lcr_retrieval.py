"""Unified, permission-scoped LCR retrieval composition.

The canonical care projection remains owned by :mod:`lcr_context`. This
composition adds bounded prior Companion dialogue as conversational context,
never as evidence or an authoritative care assertion. Every query is scoped by
both person and caregiver through the persisted conversation-session relation.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.collaboration import ConversationMessage, ConversationSession
from app.services.lcr_context import build_lcr_context


MAX_CONVERSATION_CONTEXT_ITEMS = 8
MAX_CONVERSATION_MESSAGE_CHARS = 1200
MAX_QUERY_TERMS = 8


def _query_terms(query: str | None) -> list[str]:
    import re

    return list(dict.fromkeys(
        term for term in re.findall(r"[A-Za-z0-9_-]{3,}", (query or "").casefold())
    ))[:MAX_QUERY_TERMS]


def _conversation_context(
    db: Session,
    *,
    person_id: str,
    caregiver_id: str,
    query: str | None,
    limit: int,
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
    terms = _query_terms(query)
    if terms:
        statement = statement.filter(or_(*[
            ConversationMessage.content.ilike(f"%{term}%") for term in terms
        ]))
    rows = (
        statement.order_by(ConversationMessage.created_at.desc(), ConversationMessage.id.desc())
        .limit(max(1, min(int(limit), MAX_CONVERSATION_CONTEXT_ITEMS)) + 1)
        .all()
    )
    bounded = rows[:max(1, min(int(limit), MAX_CONVERSATION_CONTEXT_ITEMS))]
    items = []
    for message, conversation in bounded:
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
    items.sort(key=lambda item: (item.get("recorded_at") or "", item["id"]))
    return {
        "status": "available" if items else "empty",
        "table": "conversation_messages",
        "items": items,
        "returned_count": len(items),
        "has_more": len(rows) > len(bounded),
        "limit": max(1, min(int(limit), MAX_CONVERSATION_CONTEXT_ITEMS)),
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
) -> dict[str, Any]:
    """Compose canonical care sources and authorized prior Companion dialogue.

    Authorization must already have been established by the request's server-side
    session scope. The caregiver/person pair is repeated in the conversation query
    so a caller cannot broaden dialogue retrieval by supplying a session ID.
    """
    if not person_id or not caregiver_id:
        raise ValueError("Authorized person_id and caregiver_id are required")

    context = build_lcr_context(
        db, str(person_id), limit_per_source=limit_per_source, query=query
    )
    conversation = _conversation_context(
        db,
        person_id=str(person_id),
        caregiver_id=str(caregiver_id),
        query=query,
        limit=conversation_limit,
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
    }
    return context
