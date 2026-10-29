"""Permission-scoped, bounded retrieval shared by LCR-facing workflows.

This module is read-only. It projects existing canonical care-domain tables into a
single response contract; it does not promote assertions, create facts, or mutate
the Living Care Record.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime
from functools import lru_cache
from typing import Any

from sqlalchemy import MetaData, Table, desc, inspect, or_, select
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
        "fields": [
            "id", "type", "evidence_kind", "epistemic_status", "source_text", "uploaded_at",
            "uploaded_by_caregiver_id", "time_provenance", "location", "evidence_status",
            "source_type", "source_span_start", "source_span_end", "original_author",
            "reported_by_label", "reported_by_caregiver_id", "collected_by_caregiver_id",
            "collected_at", "occurred_at", "occurred_at_timezone", "origin_channel",
            "original_source_reference", "parent_evidence_id", "source_text_sha256",
            "ingested_at", "reported_at", "collection_method", "origin_device_ref",
            "transfer_source", "transfer_reference",
        ],
        "order": ["uploaded_at", "created_at"],
    },
    "evidence_observations": {
        "table": "evidence_observations",
        "required": False,
        "fields": ["id", "evidence_id", "supersedes_observation_id", "correction_rationale",
                   "observation_kind", "epistemic_status", "subject_label", "attribute", "value_text",
                   "normalized_value", "units", "context", "occurred_at", "observed_at", "reported_at",
                   "observer_label", "observer_caregiver_id", "observation_method", "source_passage",
                   "source_span_start", "source_span_end", "negated", "hedge_marker", "ambiguity_note",
                   "extraction_method", "extraction_version", "extraction_run_id",
                   "created_by_caregiver_id", "recorded_at"],
        # Prefer recording order so a recent correction is not hidden behind
        # an older event-time value in a bounded source projection.
        "order": ["recorded_at", "created_at", "occurred_at"],
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
    "uncertainty_assessments": {
        "table": "uncertainty_assessments",
        "required": False,
        "fields": ["id", "subject_type", "subject_id", "field", "epistemic_state", "confidence",
                   "known_value", "gap_reason", "context", "evidence_ids", "temporal_precision",
                   "assessed_at", "assessed_by_caregiver_id", "superseded_by_assessment_id", "is_active"],
        "order": ["assessed_at"],
    },
    "missing_information": {
        "table": "care_missing_information",
        "required": False,
        "fields": ["id", "care_record_id", "topic", "description", "priority", "status", "created_at", "resolved_at"],
        "order": ["created_at"],
    },
    "open_questions": {
        "table": "open_questions",
        "required": False,
        "fields": ["id", "question", "status", "priority", "evidence_ids", "asked_at", "answered_at", "answer", "closed_at", "closed_reason", "created_by_caregiver_id"],
        "order": ["asked_at"],
    },
    "open_loop_records": {
        "table": "open_loop_records",
        "required": False,
        "fields": [
            "id", "loop_type", "title", "description", "status", "outcome_status",
            "owner_role", "owner_label", "uncertainty", "missing_information", "options",
            "request_text", "recipient_label", "requested_at", "last_contact_at",
            "verification_rule", "deadline_at", "deadline_source", "deadline_timezone",
            "consequence_of_delay", "priority", "priority_reasons", "deferred_reason",
            "review_at", "inactive_since", "closure_result", "closed_at",
            "cancellation_reason", "recurrence_of_id", "created_at", "updated_at", "version",
        ],
        "order": ["updated_at", "created_at"],
    },
    "information_gaps": {
        "table": "information_gaps",
        "required": False,
        "fields": ["id", "subject_type", "subject_id", "field", "epistemic_state", "gap_reason", "priority", "lifecycle_status", "description", "expected_by", "resolution_date", "resolution_notes", "evidence_ids", "created_at", "updated_at", "created_by_caregiver_id"],
        "order": ["created_at"],
    },
    "follow_up_links": {
        "table": "care_follow_up_links",
        "required": False,
        "fields": ["id", "source_type", "source_id", "target_type", "target_id", "follow_up_kind", "status", "due_at", "completed_at", "notes", "evidence_ids", "created_by_caregiver_id", "created_at"],
        "order": ["created_at"],
    },
    "reconciliations": {
        "table": "care_reconciliation_records",
        "required": False,
        "fields": ["id", "target_type", "target_id", "reconciliation_status", "competing_claim_ids", "evidence_ids", "decision_notes", "reviewed_by_caregiver_id", "reviewed_at", "created_at"],
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
    "corrections": {
        "table": "corrections",
        "required": False,
        "fields": ["id", "target_type", "target_id", "original_text", "corrected_text", "reason",
                   "caregiver_id", "created_at"],
        "order": ["created_at"],
    },
    "appointments": {
        "table": "appointments",
        "required": False,
        "fields": ["id", "title", "status", "scheduled_at", "description", "created_at", "updated_at"],
        "order": ["scheduled_at", "created_at"],
    },
}

SEARCH_FIELDS = {
    "living_record": ["record_status"],
    "events": ["event_type", "title", "description"],
    "evidence": ["type", "source_text", "source_type"],
    "evidence_observations": ["observation_kind", "epistemic_status", "subject_label", "attribute", "value_text", "context", "source_passage", "ambiguity_note"],
    "facts": ["subject_type", "fact_type", "fact_value", "review_notes"],
    "assertions": ["subject_type", "field", "asserted_value", "source_label", "source_text", "assertion_status"],
    "decisions": ["title", "decision_text", "context", "decision_status"],
    "decision_outcomes": ["outcome_status", "description"],
    "timeline": ["entry_type", "title", "summary", "epistemic_status"],
    "uncertainties": ["question", "uncertainty_type", "status"],
    "uncertainty_assessments": ["subject_type", "subject_id", "field", "epistemic_state", "gap_reason", "known_value"],
    "missing_information": ["topic", "description", "priority", "status"],
    "conflicts": ["conflict_type", "description", "resolution", "status"],
    "provenance": ["source_type", "author_label", "method", "reason"],
    "caregiver_observations": ["observation_category", "description", "source_type"],
    "caregiver_contexts": ["context_type", "notes", "epistemic_status"],
    "caregiver_perspectives": ["topic", "perspective_text", "epistemic_status"],
    "corrections": ["target_type", "original_text", "corrected_text", "reason"],
    "open_loop_records": ["loop_type", "title", "description", "status", "request_text", "recipient_label"],
    "appointments": ["title", "status", "description"],
}

TEXT_FIELDS = {
    "source_text", "fact_value", "asserted_value", "source_label", "source_span", "description",
    "decision_text", "context", "summary", "title", "question", "topic", "perspective_text",
    "notes", "reason", "review_notes", "resolution",
}
MAX_TEXT_CHARS = 1600
JSON_TEXT_FIELDS = {
    "evidence_ids", "source_ids", "claim_refs", "competing_claim_ids",
    "context", "metadata", "context_payload", "missing_information", "options", "priority_reasons",
}


def _safe_value(value: Any, key: str | None = None) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _safe_value(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe_value(v, key) for v in value]
    if isinstance(value, str) and key in JSON_TEXT_FIELDS and value.lstrip().startswith(("[", "{")):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, (list, dict)):
                return _safe_value(parsed, key)
        except (TypeError, json.JSONDecodeError):
            pass
    if isinstance(value, str) and key in TEXT_FIELDS and len(value) > MAX_TEXT_CHARS:
        return value[:MAX_TEXT_CHARS] + "… [truncated]"
    return value


@lru_cache(maxsize=16)
def _load_source_schema(bind: Any) -> tuple[frozenset[str], MetaData]:
    """Reflect the care-source schema once per SQLAlchemy engine.

    The API process is restarted after deployment migrations, so the cache is
    naturally refreshed on schema rollout. Keeping inspection out of each request
    avoids repeating database catalog queries and table reflection on hot paths.
    The returned metadata is treated as immutable by callers.
    """
    available_tables = frozenset(inspect(bind).get_table_names())
    metadata = MetaData()
    present = [
        spec["table"]
        for spec in SOURCE_SPECS.values()
        if spec["table"] in available_tables
    ]
    # Source lineage is auxiliary metadata for all LCR-facing workflows. Keep it
    # outside SOURCE_SPECS so its absence does not falsely mark core schema coverage
    # as incomplete on legacy databases.
    if "evidence_source_relationships" in available_tables:
        present.append("evidence_source_relationships")
    if present:
        metadata.reflect(bind=bind, only=list(dict.fromkeys(present)))
    return available_tables, metadata


def build_lcr_context(db: Session, person_id: str, *, limit_per_source: int = 20, query: str | None = None) -> dict[str, Any]:
    """Return a bounded, person-scoped LCR projection and explicit schema coverage.

    When query is provided, each source is searched with bounded text terms before
    applying its per-source limit; unavailable sources remain explicitly marked.
    """
    limit = max(1, min(int(limit_per_source), 50))
    terms = list(dict.fromkeys(re.findall(r"[A-Za-z0-9_-]{3,}", (query or "").casefold())))[:8]
    bind = db.get_bind()
    available_tables, metadata = _load_source_schema(bind)

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
        statement = select(*selected_columns).where(table.c.person_id == str(person_id))
        if source_name == "uncertainty_assessments" and "is_active" in table.c:
            statement = statement.where(table.c.is_active.is_(True))
        search_columns = [
            table.c[name] for name in SEARCH_FIELDS.get(source_name, [])
            if name in table.c
        ]
        if terms and search_columns:
            statement = statement.where(or_(*[
                column.ilike(f"%{term}%")
                for column in search_columns
                for term in terms
            ]))
        order_field = next((name for name in spec["order"] if name in table.c), None)
        if order_field:
            statement = statement.order_by(desc(table.c[order_field]))
        if "id" in table.c and order_field != "id":
            statement = statement.order_by(desc(table.c.id))
        rows = db.execute(statement.limit(limit + 1)).mappings().all()
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

    evidence_lineage: dict[str, Any] = {
        "status": "not_assessed",
        "groups": [],
        "relationship_records_examined": 0,
        "relationship_history_bounded": True,
        "interpretation_limit": (
            "Source lineage is based only on explicit parent/source references and persisted "
            "reviewed relationships. Text similarity, shared vocabulary, or model repetition "
            "does not establish common origin or independent corroboration."
        ),
    }
    evidence_source = sources.get("evidence", {})
    evidence_items = evidence_source.get("items", [])
    relationship_table = metadata.tables.get("evidence_source_relationships")
    if relationship_table is not None and "person_id" in relationship_table.c:
        relationship_rows = db.execute(
            select(*relationship_table.c).where(
                relationship_table.c.person_id == str(person_id)
            ).order_by(
                desc(relationship_table.c.created_at)
                if "created_at" in relationship_table.c else relationship_table.c.id
            ).limit(min(limit * 3, 150) + 1)
        ).mappings().all()
        relationship_has_more = len(relationship_rows) > min(limit * 3, 150)
        relationship_rows = relationship_rows[:min(limit * 3, 150)]
        from types import SimpleNamespace
        from app.services.evidence_lineage import build_evidence_lineage
        selected_ids = [str(item.get("id")) for item in evidence_items if item.get("id")]
        lineage_by_id, independence_by_id = build_evidence_lineage(
            [SimpleNamespace(**item) for item in evidence_items],
            [SimpleNamespace(**dict(row)) for row in relationship_rows],
            selected_ids,
        )
        group_members: dict[str, list[str]] = {}
        for evidence_id, lineage_id in lineage_by_id.items():
            group_members.setdefault(lineage_id, []).append(evidence_id)
        evidence_lineage.update({
            "status": "assessed_bounded_subset",
            "groups": [
                {
                    "lineage_id": lineage_id,
                    "evidence_ids": sorted(ids),
                    "source_independence_status": (
                        "shared_origin" if any(independence_by_id.get(eid) == "shared_origin" for eid in ids)
                        else "unassessed"
                    ),
                    "origin_group_keys": sorted({
                        str(getattr(link, "origin_group_key", "") or "").strip()
                        for link in [SimpleNamespace(**dict(row)) for row in relationship_rows]
                        if str(getattr(link, "origin_group_key", "") or "").strip()
                        and str(getattr(link, "source_evidence_id", "") or "") in ids
                        or str(getattr(link, "related_evidence_id", "") or "") in ids
                    }),
                    "basis": "explicit_persisted_provenance",
                    "interpretation_limit": "A lineage link is not proof that the linked claim is true.",
                }
                for lineage_id, ids in sorted(group_members.items())
            ],
            "relationship_records_examined": len(relationship_rows),
            "relationship_history_bounded": relationship_has_more,
        })
    else:
        evidence_lineage["status"] = "unavailable_schema" if evidence_items else "not_assessed"

    schema_status = "complete" if not missing_required and not missing_optional else "partial"
    return {
        "person_id": str(person_id),
        "as_of": datetime.now().astimezone().isoformat(),
        "read_only": True,
        "history_coverage": "bounded_subset",
        "query_applied": bool(terms),
        "schema_status": schema_status,
        "missing_required_sources": missing_required,
        "missing_optional_sources": missing_optional,
        "sources": sources,
        "evidence_lineage": evidence_lineage,
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
            "evidence_lineage": context.get("evidence_lineage", {
                "status": "not_assessed",
                "interpretation_limit": "No source-lineage assessment was supplied.",
            }),
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
