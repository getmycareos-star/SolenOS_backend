"""Permission-scoped, bounded retrieval shared by LCR-facing workflows.

This module is read-only. It projects existing canonical care-domain tables into a
single response contract; it does not promote assertions, create facts, or mutate
the Living Care Record.
"""
from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

from sqlalchemy import MetaData, Table, desc, inspect, select
from sqlalchemy.orm import Session


# Only caregiver-relevant fields are exposed. Internal storage paths, raw tokens,
# and operational secrets are deliberately excluded.
SOURCE_SPECS: dict[str, dict[str, Any]] = {
    "living_record": {
        "table": "living_care_records",
        "required": True,
        "fields": ["id", "record_status", "schema_version", "created_at", "updated_at", "created_by_caregiver_id"],
        "order": ["updated_at", "created_at"],
    },
    "events": {
        "table": "care_events",
        "required": True,
        "fields": ["id", "event_type", "status", "occurred_at", "recorded_at", "title", "description",
                   "evidence_ids", "time_provenance", "location_provenance", "created_by_caregiver_id",
                   "significance_verdict", "attention_candidate", "follow_up_candidate", "situation_id"],
        "order": ["occurred_at", "recorded_at", "created_at"],
    },
    "evidence": {
        "table": "evidence",
        "required": True,
        "fields": ["id", "type", "source_text", "uploaded_at", "uploaded_by_caregiver_id",
                   "time_provenance", "location", "evidence_status", "source_type",
                   "source_span_start", "source_span_end"],
        "order": ["uploaded_at", "created_at"],
    },
    "facts": {
        "table": "care_facts",
        "required": True,
        "fields": ["id", "subject_type", "subject_id", "fact_type", "fact_value", "epistemic_status",
                   "effective_at", "recorded_at", "created_at", "superseded_by_fact_id",
                   "created_by_caregiver_id", "review_notes"],
        "order": ["recorded_at", "created_at", "effective_at"],
    },
    "assertions": {
        "table": "care_assertions",
        "required": True,
        "fields": ["id", "subject_type", "subject_id", "field", "assertion_kind", "asserted_value",
                   "source_event_id", "source_label", "source_text", "evidence_ids", "assertion_status",
                   "observed_at", "review_after_days", "created_by_caregiver_id", "recorded_at"],
        "order": ["recorded_at", "created_at"],
    },
    "decisions": {
        "table": "care_decisions",
        "required": True,
        "fields": ["id", "title", "decision_text", "decision_status", "decided_at", "context",
                   "evidence_ids", "created_at", "created_by_caregiver_id"],
        "order": ["decided_at", "created_at"],
    },
    "decision_outcomes": {
        "table": "decision_outcomes",
        "required": False,
        "fields": ["id", "decision_id", "outcome_status", "observed_at", "description",
                   "evidence_ids", "is_intended_outcome", "created_at"],
        "order": ["observed_at", "created_at"],
    },
    "timeline": {
        "table": "care_timeline_entries",
        "required": False,
        "fields": ["id", "source_type", "source_id", "entry_type", "title", "summary",
                   "occurred_at", "evidence_ids", "epistemic_status", "is_projection", "created_at"],
        "order": ["occurred_at", "created_at"],
    },
    "uncertainties": {
        "table": "care_uncertainties",
        "required": False,
        "fields": ["id", "care_record_id", "target_type", "target_id", "question", "uncertainty_type",
                   "status", "source_ids", "created_by_caregiver_id", "created_at", "resolved_at"],
        "order": ["created_at"],
    },
    "missing_information": {
        "table": "care_missing_information",
        "required": False,
        "fields": ["id", "care_record_id", "topic", "description", "priority", "status", "created_at", "resolved_at"],
        "order": ["created_at"],
    },
    "conflicts": {
        "table": "care_conflicts",
        "required": False,
        "fields": ["id", "care_record_id", "conflict_type", "status", "claim_refs", "description",
                   "resolution", "resolved_by_caregiver_id", "created_at", "resolved_at"],
        "order": ["created_at"],
    },
    "provenance": {
        "table": "record_provenance",
        "required": False,
        "fields": ["id", "care_record_id", "source_type", "source_id", "author_caregiver_id",
                   "author_label", "method", "recorded_at", "occurred_at", "effective_from",
                   "effective_to", "reason", "metadata", "created_at"],
        "order": ["recorded_at", "created_at"],
    },
    "caregiver_observations": {
        "table": "caregiver_observations",
        "required": False,
        "fields": ["id", "observation_category", "description", "observed_value", "observed_at",
                   "recorded_at", "epistemic_status", "evidence_ids", "source_type", "source_id",
                   "reported_by_caregiver_id", "created_at"],
        "order": ["observed_at", "recorded_at", "created_at"],
    },
    "caregiver_contexts": {
        "table": "caregiver_contexts",
        "required": False,
        "fields": ["id", "context_type", "context_payload", "epistemic_status", "source_type",
                   "source_id", "recorded_at", "effective_from", "effective_to", "notes",
                   "reported_by_caregiver_id", "created_at"],
        "order": ["recorded_at", "created_at"],
    },
    "caregiver_perspectives": {
        "table": "caregiver_perspectives",
        "required": False,
        "fields": ["id", "topic", "perspective_text", "epistemic_status", "evidence_ids",
                   "source_type", "source_id", "recorded_at", "effective_from", "effective_to",
                   "supersedes_id", "reported_by_caregiver_id", "created_at"],
        "order": ["recorded_at", "created_at"],
    },
}

TEXT_FIELDS = {
    "source_text", "fact_value", "asserted_value", "source_label", "source_span", "description",
    "decision_text", "context", "summary", "title", "question", "topic", "perspective_text",
    "notes", "reason", "review_notes", "resolution",
}
MAX_TEXT_CHARS = 1600


def _safe_value(value: Any, key: str | None = None) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _safe_value(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe_value(v, key) for v in value]
    if isinstance(value, str) and key in TEXT_FIELDS and len(value) > MAX_TEXT_CHARS:
        return value[:MAX_TEXT_CHARS] + "… [truncated]"
    return value


def build_lcr_context(db: Session, person_id: str, *, limit_per_source: int = 20) -> dict[str, Any]:
    """Return a bounded, person-scoped LCR projection and explicit schema coverage."""
    limit = max(1, min(int(limit_per_source), 50))
    bind = db.get_bind()
    inspector = inspect(bind)
    available_tables = set(inspector.get_table_names())
    metadata = MetaData()
    present = [spec["table"] for spec in SOURCE_SPECS.values() if spec["table"] in available_tables]
    if present:
        metadata.reflect(bind=bind, only=list(dict.fromkeys(present)))

    sources: dict[str, dict[str, Any]] = {}
    missing_required: list[str] = []
    missing_optional: list[str] = []

    for source_name, spec in SOURCE_SPECS.items():
        table_name = spec["table"]
        if table_name not in available_tables:
            (missing_required if spec["required"] else missing_optional).append(source_name)
            sources[source_name] = {
                "status": "unavailable_schema",
                "table": table_name,
                "items": [],
                "returned_count": 0,
                "has_more": False,
                "limit": limit,
            }
            continue

        table: Table = metadata.tables[table_name]
        if "person_id" not in table.c:
            (missing_required if spec["required"] else missing_optional).append(source_name)
            sources[source_name] = {
                "status": "unavailable_scope",
                "table": table_name,
                "items": [],
                "returned_count": 0,
                "has_more": False,
                "limit": limit,
            }
            continue

        field_names = [name for name in spec["fields"] if name in table.c]
        if "id" in table.c and "id" not in field_names:
            field_names.insert(0, "id")
        selected_columns = [table.c[name] for name in field_names]
        query = select(*selected_columns).where(table.c.person_id == str(person_id))
        order_field = next((name for name in spec["order"] if name in table.c), None)
        if order_field:
            query = query.order_by(desc(table.c[order_field]))
        if "id" in table.c and order_field != "id":
            query = query.order_by(desc(table.c.id))
        rows = db.execute(query.limit(limit + 1)).mappings().all()
        has_more = len(rows) > limit
        items = [
            {key: _safe_value(value, key) for key, value in dict(row).items()}
            for row in rows[:limit]
        ]
        sources[source_name] = {
            "status": "available" if items else "empty",
            "table": table_name,
            "items": items,
            "returned_count": len(items),
            "has_more": has_more,
            "limit": limit,
        }

    schema_status = "complete" if not missing_required and not missing_optional else "partial"
    return {
        "person_id": str(person_id),
        "as_of": datetime.now().astimezone().isoformat(),
        "read_only": True,
        "history_coverage": "bounded_subset",
        "schema_status": schema_status,
        "missing_required_sources": missing_required,
        "missing_optional_sources": missing_optional,
        "sources": sources,
    }


def format_lcr_context_for_prompt(context: dict[str, Any], *, max_chars: int = 14000) -> str:
    """Serialize context as bounded labelled data, never as instruction-bearing messages."""
    header = (
        "LCR context metadata: "
        + json.dumps({
            "schema_status": context.get("schema_status"),
            "history_coverage": context.get("history_coverage"),
            "missing_required_sources": context.get("missing_required_sources", []),
            "missing_optional_sources": context.get("missing_optional_sources", []),
        }, ensure_ascii=False, separators=(",", ":"))
    )
    chunks = [header]
    current_length = len(header)
    for source_name, source in context.get("sources", {}).items():
        section = f"\n[{source_name}; status={source.get('status')}; returned={source.get('returned_count', 0)}; has_more={source.get('has_more', False)}]"
        if current_length + len(section) > max_chars:
            chunks.append("\n[Context truncated because the safe prompt budget was reached.]")
            break
        chunks.append(section)
        current_length += len(section)
        for item in source.get("items", []):
            line = json.dumps(item, ensure_ascii=False, default=str, separators=(",", ":"))
            if current_length + len(line) + 1 > max_chars:
                chunks.append("\n[Context truncated because the safe prompt budget was reached.]")
                return "".join(chunks)
            chunks.extend(["\n", line])
            current_length += len(line) + 1
    return "".join(chunks)
