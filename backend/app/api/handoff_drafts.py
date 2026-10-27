"""Read-only, session-scoped care handoff draft orchestration.

This endpoint only reads a fixed allowlist of persisted care-continuity tables.
It does not create a handoff, attach a summary, or mutate the Living Care Record.
"""
from datetime import date, datetime, timedelta, timezone
from typing import Literal
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import MetaData, Table, inspect, select, func, or_, and_
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
        "fields": ("id", "subject_type", "subject_id", "fact_type", "fact_value", "epistemic_status", "effective_at", "recorded_at", "created_at", "updated_at", "superseded_by_fact_id", "review_notes"),
        "text_fields": ("fact_value", "review_notes"),
        "time_fields": ("recorded_at", "created_at", "updated_at"),
        "event_time_fields": ("effective_at",),
        "recorded_time_fields": ("recorded_at", "created_at", "updated_at"),
        "detail_fields": ("fact_type", "subject_type", "superseded_by_fact_id"),
        "include_active_outside_window": True,
    },
    "care_decisions": {
        "label": "Care decisions",
        "kind": "care_decision",
        "fields": ("id", "title", "decision_text", "decision_status", "decided_at", "created_at", "updated_at", "context"),
        "text_fields": ("decision_text", "title", "context"),
        "time_fields": ("created_at", "updated_at"),
        "recorded_time_fields": ("created_at", "updated_at"),
        "detail_fields": ("status",),
        "event_time_fields": ("decided_at",),
        "recorded_time_fields": ("created_at", "updated_at"),
        "detail_fields": ("decision_status",),
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
        "recorded_time_fields": ("created_at", "updated_at"),
        "detail_fields": ("priority", "status"),
    },
    "care_timeline_entries": {
        "label": "Care timeline",
        "kind": "timeline_entry",
        "fields": ("id", "entry_type", "title", "summary", "occurred_at", "epistemic_status", "created_at"),
        "text_fields": ("title", "summary", "entry_type"),
        "time_fields": ("occurred_at", "created_at"),
        "event_time_fields": ("occurred_at",),
        "recorded_time_fields": ("created_at",),
        "detail_fields": ("transition_type", "from_location", "to_location", "location_provenance"),
        "event_time_fields": ("occurred_at",),
        "recorded_time_fields": ("created_at",),
        "detail_fields": ("entry_type",),
    },
    "care_events": {
        "label": "Recorded care events",
        "kind": "care_event",
        "fields": ("id", "event_type", "status", "occurred_at", "recorded_at", "title", "description", "time_provenance", "confidence"),
        "text_fields": ("title", "description", "event_type", "time_provenance"),
        "time_fields": ("occurred_at", "recorded_at"),
        "event_time_fields": ("occurred_at",),
        "recorded_time_fields": ("recorded_at",),
        "detail_fields": ("event_type", "confidence"),
    },
    "care_transitions": {
        "label": "Care transitions",
        "kind": "care_transition",
        "fields": ("id", "from_location", "to_location", "transition_type", "occurred_at", "created_at", "notes", "time_provenance", "location_provenance"),
        "text_fields": ("transition_type", "from_location", "to_location", "notes", "time_provenance", "location_provenance"),
        "time_fields": ("occurred_at", "created_at"),
    },
    "appointments": {
        "label": "Appointments",
        "kind": "appointment",
        "fields": ("id", "title", "scheduled_at", "scheduled_at_timezone", "location", "provider", "healthcare_org", "preparation_notes", "medication_questions", "travel_notes", "status", "time_provenance", "location_provenance", "created_at", "updated_at"),
        "text_fields": ("title", "provider", "healthcare_org", "location", "preparation_notes", "medication_questions", "travel_notes", "time_provenance", "location_provenance"),
        "time_fields": ("scheduled_at", "created_at", "updated_at"),
        "event_time_fields": ("scheduled_at",),
        "recorded_time_fields": ("created_at", "updated_at"),
        "detail_fields": ("scheduled_at_timezone", "provider", "healthcare_org", "location", "status", "preparation_notes", "medication_questions", "travel_notes", "time_provenance", "location_provenance"),
    },
    "actions": {
        "label": "Recorded care actions",
        "kind": "care_action",
        "fields": ("id", "source_evidence_id", "source_passage", "source_authority", "actor_label", "subject_label", "action_type", "action_object", "normalized_action", "original_text", "modality", "condition_text", "trigger_text", "deadline", "start_time", "status", "provenance_notes", "created_at", "updated_at"),
        "text_fields": ("normalized_action", "original_text", "action_object", "actor_label", "subject_label", "condition_text", "trigger_text", "provenance_notes"),
        "time_fields": ("created_at", "updated_at"),
        "event_time_fields": ("start_time",),
        "recorded_time_fields": ("created_at", "updated_at"),
        "due_time_fields": ("deadline",),
        "detail_fields": ("source_authority", "actor_label", "subject_label", "action_type", "modality", "condition_text", "trigger_text", "deadline", "status", "source_evidence_id"),
        "require_caregiver_owned_evidence": True,
    },
}

class DraftRequest(BaseModel):
    purpose: Literal[
        "family_transition", "professional_appointment", "hospital_discharge",
        "return_to_care", "period_summary"
    ] = "family_transition"
    audience: Literal["family", "professional"] = "family"
    window_days: int = Field(default=30, ge=1, le=MAX_DAYS)


# Templates define what a handoff should cover; they do not manufacture missing
# values. Coverage is explicitly computed from source adapter availability only.
TEMPLATES = {
    "family": [
        ("start_here", "What matters most right now", ("care_facts", "care_timeline_entries", "care_events", "care_transitions"), ("Current situation", "Immediate care needs", "What the next caregiver must know")),
        ("current_status", "How the person is doing", ("care_facts", "care_timeline_entries", "care_events"), ("Today", "Change from baseline", "Uncertainty")),
        ("daily_routine", "How to care for them", (), ("Daily routine", "Communication preferences", "Comfort and preferences", "Known distress triggers and helpful responses")),
        ("health_medication", "Health and medication information", ("appointments",), ("Documented health concerns", "Verified medication list", "Allergies and reactions", "Appointments and clinician instructions")),
        ("next_actions", "What needs to happen next", ("actions", "care_decisions", "open_questions", "information_gaps"), ("Action", "Named owner", "Due time/date", "Status", "Outcome")),
        ("safety_contingencies", "Safety and when to seek help", (), ("Documented safety needs", "Existing care-plan instructions", "Emergency and clinical contacts")),
        ("family_coordination", "Family coordination", (), ("Primary caregiver", "Backup caregiver", "Next check-in", "Practical support needs")),
        ("receipt", "Handoff acceptance", (), ("Information reviewed", "Responsibilities understood", "Unresolved questions", "Outgoing and receiving caregiver confirmation")),
    ],
    "professional": [
        ("transition_summary", "Executive summary and current situation", ("care_facts", "care_timeline_entries", "care_events", "care_transitions"), ("Reason for handoff", "Current situation", "Immediate priorities", "Clinician-assessed acuity if documented")),
        ("baseline", "Relevant history and baseline", ("care_facts", "care_timeline_entries", "care_events"), ("Documented diagnoses and source", "Relevant history", "Cognitive and functional baseline", "Changes from baseline", "Person's goals and preferences")),
        ("provenance", "Evidence and provenance register", ("care_facts", "care_timeline_entries", "care_events", "care_transitions", "appointments", "actions", "care_decisions", "open_questions", "information_gaps"), ("Source ID", "Author/reporter", "Timestamp", "Evidence status", "Conflicts", "Corrections")),
        ("medication_reconciliation", "Medication reconciliation", (), ("Verified medication and regimen", "Nonprescription products", "Allergies/reactions", "Recent authorised changes", "Discrepancies", "Resolution owner", "Reconciliation status")),
        ("clinical_functional", "Current clinical and functional assessment", (), ("Observations and measurements", "Symptoms and events", "Cognition/communication", "Mobility/ADLs", "Nutrition/hydration", "Sleep/mood/behaviour", "Equipment/environment")),
        ("problems_uncertainty", "Active problems, risks, and uncertainty", ("open_questions", "information_gaps", "care_facts", "care_events", "actions"), ("Issue", "Evidence/status", "Required response", "Owner", "Deadline")),
        ("care_plan", "Treatment and care plan", ("care_decisions", "actions"), ("Authorised plan", "Care delivered", "Response", "Pending investigations/consultations", "Unresolved decisions", "Escalation instructions")),
        ("action_register", "Action register and accountability", ("actions", "care_decisions", "open_questions", "information_gaps"), ("Action", "Owner", "Deadline", "Status", "Outcome")),
        ("person_family_context", "Person-centred and family context", (), ("Communication preferences", "Relevant routines", "Authorised contacts", "Caregiver support needs", "Documented information-sharing basis")),
        ("transition_details", "Transition-specific information", ("care_transitions", "appointments", "care_events"), ("Sending/receiving setting", "Reason for transfer", "Course/interventions", "Discharge instructions", "Follow-ups", "Pending results", "Equipment/supplies")),
        ("contingency", "Contingency planning", (), ("Documented trigger/concern", "Existing authorised response", "Escalation contact", "Emergency pathway", "Limitations")),
        ("integrity", "Document and record integrity", ("care_facts", "care_timeline_entries", "care_events", "care_transitions", "appointments", "actions", "care_decisions", "open_questions", "information_gaps"), ("Source record IDs/versions", "Generated timestamp/timezone", "Last verified", "Known gaps/stale information")),
        ("receipt", "Handoff receipt and acceptance", (), ("Sender/receiver identity and role", "Receipt timestamp", "Responsibility transfer time", "Critical items confirmed", "Questions/clarifications", "Unaccepted actions", "Acceptance status")),
    ],
}


def _template_coverage(audience: str, statuses: dict) -> list[dict]:
    template = TEMPLATES[audience]
    result = []
    for key, title, source_tables, fields in template:
        if not source_tables:
            coverage = "not_assessed"
        else:
            source_states = [statuses.get(name, {}).get("status", "unavailable") for name in source_tables]
            if any(value == "available" for value in source_states):
                coverage = "partial"
            elif any(value == "empty" for value in source_states):
                coverage = "no_records_returned"
            else:
                coverage = "unavailable"
        result.append({
            "key": key,
            "title": title,
            "required_fields": list(fields),
            "source_tables": list(source_tables),
            "coverage_status": coverage,
            "coverage_note": "Source coverage only; each field still requires review and may be missing or unverified.",
        })
    return result


def _iso(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _detail_value(value):
    if value is None or isinstance(value, dict):
        return None
    if isinstance(value, (datetime, date)):
        return _iso(value)
    if isinstance(value, list):
        scalar_values = [str(item).strip() for item in value if isinstance(item, (str, int, float, bool))]
        return "; ".join(scalar_values)[:500] if scalar_values else None
    text = str(value).strip()
    return text[:500] if text else None


def _display_text(row: dict, fields: tuple[str, ...]) -> str:
    parts = []
    for key in fields:
        value = row.get(key)
        if value is None or isinstance(value, dict):
            continue
        if isinstance(value, list):
            scalar_values = [str(item).strip() for item in value if isinstance(item, (str, int, float, bool))]
            if not scalar_values:
                continue
            text = "; ".join(scalar_values)
        else:
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

        if spec.get("require_caregiver_owned_evidence"):
            if not inspector.has_table("evidence"):
                statuses[table_name] = {
                    "status": "unavailable",
                    "reason": "evidence_scope_adapter_unavailable",
                    "label": spec["label"],
                    "count": 0,
                }
                continue
            evidence_table = Table("evidence", metadata, autoload_with=bind)
            if not {"id", "person_id", "uploaded_by_caregiver_id"}.issubset(set(evidence_table.c.keys())) or "source_evidence_id" not in table.c:
                statuses[table_name] = {
                    "status": "unavailable",
                    "reason": "evidence_scope_not_verifiable",
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
        if spec.get("require_caregiver_owned_evidence"):
            query = query.join(evidence_table, table.c.source_evidence_id == evidence_table.c.id).where(
                evidence_table.c.person_id == person_id,
                evidence_table.c.uploaded_by_caregiver_id == caregiver_id,
            )
        timestamp_columns = [table.c[name] for name in spec["time_fields"] if name in table.c]
        if timestamp_columns:
            # Include a row if any applicable time is within the window, or all
            # its time fields are unknown. COALESCE would hide a recent update
            # behind an older non-null creation timestamp.
            in_window = or_(
                *[column >= cutoff for column in timestamp_columns],
                and_(*[column.is_(None) for column in timestamp_columns]),
            )
            if spec.get("include_active_outside_window") and "superseded_by_fact_id" in table.c:
                query = query.where(or_(table.c.superseded_by_fact_id.is_(None), in_window))
            else:
                query = query.where(in_window)
            query = query.order_by(*[column.desc().nullslast() for column in reversed(timestamp_columns)])
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
            event_time = next((row.get(k) for k in spec.get("event_time_fields", ()) if row.get(k) is not None), None)
            recorded_time = next((row.get(k) for k in spec.get("recorded_time_fields", spec["time_fields"]) if row.get(k) is not None), None)
            due_time = next((row.get(k) for k in spec.get("due_time_fields", ()) if row.get(k) is not None), None)
            text = _display_text(row, spec["text_fields"])
            details = {key: value for key in spec.get("detail_fields", ()) if (value := _detail_value(row.get(key))) is not None}
            if row.get("superseded_by_fact_id"):
                details["fact_lifecycle"] = "superseded"
            elif table_name == "care_facts":
                details["fact_lifecycle"] = "current_in_lcr"
            if not text:
                continue
            epistemic_value = next((row.get(k) for k in ("epistemic_status", "assertion_status") if row.get(k) is not None), None)
            record_status = next((row.get(k) for k in ("decision_status", "status") if row.get(k) is not None), None)
            records.append({
                "source_type": spec["kind"],
                "source_table": table_name,
                "source_id": str(source_id),
                "event_at": _iso(event_time),
                "recorded_at": _iso(recorded_time),
                "due_at": _iso(due_time),
                "details": details,
                "epistemic_status": str(epistemic_value) if epistemic_value is not None else "not_recorded",
                "record_status": str(record_status) if record_status is not None else "not_recorded",
                "text": text,
                "stale_warning": bool(recorded_time is not None and _iso(recorded_time) < (now - timedelta(days=30)).isoformat()),
            })

    # These modules are intentionally not represented as read when no verified
    # persistent, person-scoped adapter is available.
    statuses["daily_care"] = {"status": "unavailable", "reason": "verified_adapter_not_implemented", "count": 0}
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
                "audience": payload.audience,
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
        "audience": payload.audience,
        "template_sections": _template_coverage(payload.audience, statuses),
        "prepared_at": now.isoformat(),
        "window": {"days": payload.window_days, "from": cutoff.isoformat(), "to": now.isoformat()},
        "read_only": True,
        "requires_caregiver_review": True,
        "creates_handoff": False,
        "updates_lcr": False,
        "source_statuses": statuses,
        "sections": sections,
        "source_references": [
            {key: item[key] for key in ("source_type", "source_table", "source_id", "event_at", "recorded_at", "due_at", "details", "epistemic_status", "record_status", "stale_warning")}
            for item in records
        ],
        "limitations": gaps,
        "warning": "This is a draft from the listed saved records only. Unavailable sources were not read. Verify every item and resolve uncertainty before sharing.",
    }
