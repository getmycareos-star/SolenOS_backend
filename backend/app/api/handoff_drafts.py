"""Read-only, session-scoped care handoff draft orchestration.

This endpoint only reads a fixed allowlist of persisted care-continuity tables.
It does not create a handoff, attach a summary, or mutate the Living Care Record.
"""
from datetime import date, datetime, timedelta, timezone
from typing import Literal
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import MetaData, Table, inspect, select, func, or_
from sqlalchemy.orm import Session

from app.api.care_delta import _scoped_identity
from app.core.database import get_db
from app.models.privacy_delivery import AuditLogEvent

router = APIRouter(prefix="/handoff-drafts", tags=["care handoff drafts"])
POLICY_VERSION = "handoff-draft-v1"
MAX_DAYS = 90
MAX_ROWS_PER_SOURCE = 40

# Fixed allowlist: no user-supplied table or column names are accepted.
SOURCES = {
    "care_facts": {
        "label": "Living Care Record facts",
        "kind": "lcr_fact",
        "fields": ("id", "fact_type", "fact_value", "epistemic_status", "effective_at", "created_at", "updated_at", "review_notes"),
        "text_fields": ("fact_value", "review_notes"),
        "time_fields": ("effective_at", "created_at", "updated_at"),
    },
    "care_decisions": {
        "label": "Care decisions",
        "kind": "care_decision",
        "fields": ("id", "title", "decision_text", "decision_status", "decided_at", "created_at", "context"),
        "text_fields": ("decision_text", "title", "context"),
        "time_fields": ("decided_at", "created_at"),
    },
    "open_questions": {
        "label": "Open questions",
        "kind": "open_question",
        "fields": ("id", "question", "description", "status", "created_at", "updated_at"),
        "text_fields": ("question", "description"),
        "time_fields": ("created_at", "updated_at"),
    },
    "information_gaps": {
        "label": "Information gaps",
        "kind": "information_gap",
        "fields": ("id", "topic", "description", "priority", "status", "created_at", "updated_at"),
        "text_fields": ("topic", "description"),
        "time_fields": ("created_at", "updated_at"),
    },
    "care_timeline_entries": {
        "label": "Care timeline",
        "kind": "timeline_entry",
        "fields": ("id", "entry_type", "title", "summary", "occurred_at", "epistemic_status", "created_at"),
        "text_fields": ("title", "summary", "entry_type"),
        "time_fields": ("occurred_at", "created_at"),
    },
}

class DraftRequest(BaseModel):
    purpose: Literal[
        "family_transition", "professional_appointment", "hospital_discharge",
        "return_to_care", "period_summary"
    ] = "family_transition"
    window_days: int = Field(default=30, ge=1, le=MAX_DAYS)


def _iso(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _display_text(row: dict, fields: tuple[str, ...]) -> str:
    parts = []
    for key in fields:
        value = row.get(key)
        if value is None or isinstance(value, (dict, list)):
            continue
        text = str(value).strip()
        if text and text not in parts:
            parts.append(text)
    return " — ".join(parts)[:1200]


@router.post("/prepare")
def prepare_handoff_draft(
    payload: DraftRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    person_id, caregiver_id = _scoped_identity(request, db)
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=payload.window_days)
    bind = db.get_bind()
    inspector = inspect(bind)
    metadata = MetaData()
    statuses = {}
    records = []

    for table_name, spec in SOURCES.items():
        if not inspector.has_table(table_name):
            statuses[table_name] = {
                "status": "unavailable",
                "reason": "source_schema_unavailable",
                "label": spec["label"],
                "count": 0,
            }
            continue
        table = Table(table_name, metadata, autoload_with=bind)
        # Refuse to query tables without server-side person scoping.
        if "person_id" not in table.c or "id" not in table.c:
            statuses[table_name] = {
                "status": "unavailable",
                "reason": "source_scope_not_verifiable",
                "label": spec["label"],
                "count": 0,
            }
            continue

        selected = [table.c[name] for name in spec["fields"] if name in table.c]
        if not selected:
            statuses[table_name] = {
                "status": "unavailable",
                "reason": "source_fields_unavailable",
                "label": spec["label"],
                "count": 0,
            }
            continue

        query = select(*selected).where(table.c.person_id == person_id)
        timestamp_columns = [table.c[name] for name in spec["time_fields"] if name in table.c]
        timestamp_expression = func.coalesce(*timestamp_columns) if timestamp_columns else None
        if timestamp_expression is not None:
            # Use the first populated timestamp, not merely the first existing
            # column, so a null effective_at cannot bypass the requested window.
            query = query.where(or_(timestamp_expression.is_(None), timestamp_expression >= cutoff))
            query = query.order_by(timestamp_expression.desc().nullslast())
        query = query.limit(MAX_ROWS_PER_SOURCE)
        rows = [dict(row) for row in db.execute(query).mappings().all()]
        source_status = "available" if rows else "empty"
        statuses[table_name] = {
            "status": source_status,
            "label": spec["label"],
            "count": len(rows),
        }
        for row in rows:
            source_id = row.get("id")
            if not source_id:
                continue
            time_value = next((row.get(k) for k in spec["time_fields"] if row.get(k) is not None), None)
            text = _display_text(row, spec["text_fields"])
            if not text:
                continue
            status_value = next((row.get(k) for k in ("epistemic_status", "decision_status", "status") if row.get(k) is not None), None)
            records.append({
                "source_type": spec["kind"],
                "source_table": table_name,
                "source_id": str(source_id),
                "recorded_at": _iso(time_value),
                "epistemic_status": str(status_value) if status_value is not None else "not_recorded",
                "text": text,
                "stale_warning": bool(time_value is not None and _iso(time_value) < (now - timedelta(days=30)).isoformat()),
            })

    # These modules are intentionally not represented as read when no verified
    # persistent, person-scoped adapter is available.
    statuses["daily_care"] = {"status": "unavailable", "reason": "verified_adapter_not_implemented", "count": 0}
    statuses["appointments"] = {"status": "unavailable", "reason": "verified_adapter_not_implemented", "count": 0}
    statuses["documents"] = {"status": "unavailable", "reason": "document_content_adapter_not_verified", "count": 0}
    statuses["community"] = {"status": "not_authorized", "reason": "community_source_not_enabled", "count": 0}

    sections = []
    for table_name, spec in SOURCES.items():
        items = [item for item in records if item["source_table"] == table_name]
        if items:
            sections.append({"title": spec["label"], "items": items})

    gaps = [
        {"source": name, "status": info["status"], "reason": info.get("reason")}
        for name, info in statuses.items()
        if info["status"] in {"unavailable", "not_authorized"}
    ]
    # Durable, content-free audit event. Fail closed if audit persistence fails.
    try:
        db.add(AuditLogEvent(
            id=str(uuid.uuid4()),
            actor_id=caregiver_id,
            subject_id=person_id,
            person_id=person_id,
            caregiver_id=caregiver_id,
            action="handoff_draft.prepare",
            target_type="handoff_draft",
            target_id=str(uuid.uuid4()),
            occurred_at=now,
            outcome="success",
            request_id=request.headers.get("x-request-id") or str(uuid.uuid4()),
            policy_version=POLICY_VERSION,
            record_metadata={
                "purpose": payload.purpose,
                "window_days": payload.window_days,
                "source_statuses": {key: value["status"] for key, value in statuses.items()},
                "source_record_count": len(records),
            },
        ))
        db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=503, detail="Draft audit could not be persisted; no draft was returned") from exc

    return {
        "draft_id": str(uuid.uuid4()),
        "purpose": payload.purpose,
        "prepared_at": now.isoformat(),
        "window": {"days": payload.window_days, "from": cutoff.isoformat(), "to": now.isoformat()},
        "read_only": True,
        "requires_caregiver_review": True,
        "creates_handoff": False,
        "updates_lcr": False,
        "source_statuses": statuses,
        "sections": sections,
        "source_references": [
            {key: item[key] for key in ("source_type", "source_table", "source_id", "recorded_at", "epistemic_status", "stale_warning")}
            for item in records
        ],
        "limitations": gaps,
        "warning": "This is a draft from the listed saved records only. Unavailable sources were not read. Verify every item and resolve uncertainty before sharing.",
    }
