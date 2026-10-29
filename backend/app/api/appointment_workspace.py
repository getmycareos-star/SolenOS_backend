"""Person-scoped appointment workflow persistence.

The workspace stores caregiver-entered notes and follow-up metadata separately from
canonical LCR facts. It never promotes a note to a verified clinical fact.
"""
import hashlib
import json
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from sqlalchemy import text, inspect

from app.core.database import get_db
from app.models.anonymous_session import AnonymousSession
from app.models.time_location import Appointment
from app.models.care import CareEvent, Evidence
from app.models.care_continuity import EvidenceSourceRelationship
from app.models.appointment_workspace import AppointmentWorkspaceRecord, AppointmentWorkspaceRevision
from app.schemas.appointment_workspace import AppointmentWorkspaceCreate, AppointmentWorkspaceUpdate

router = APIRouter(prefix="/appointment-workspaces", tags=["appointment-workspace"])


def _scope(request: Request, db: Session) -> tuple[str, str]:
    authorization = request.headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(status_code=401, detail="A valid SolenOS anonymous session is required")
    token_hash = hashlib.sha256(token.strip().encode("utf-8")).hexdigest()
    session = db.query(AnonymousSession).filter(
        AnonymousSession.token_hash == token_hash,
        AnonymousSession.revoked_at.is_(None),
        AnonymousSession.expires_at > datetime.now(timezone.utc),
    ).first()
    if session is None:
        raise HTTPException(status_code=401, detail="Anonymous session is invalid or expired")
    return str(session.person_id), str(session.caregiver_id)


def _iso(value):
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)

def _evidence_ids_from_row(row) -> list[str]:
    """Read only explicit evidence references; malformed values remain unlinked."""
    values = row.get("evidence_ids") if hasattr(row, "get") else None
    if values is None and hasattr(row, "get"):
        values = row.get("source_evidence_id")
    if isinstance(values, str):
        try:
            values = json.loads(values)
        except (TypeError, ValueError):
            values = [values] if values.strip() else []
    if isinstance(values, (list, tuple, set)):
        return list(dict.fromkeys(str(value) for value in values if isinstance(value, str) and value.strip()))
    return [str(values)] if isinstance(values, str) and values.strip() else []


def _record_out(record: AppointmentWorkspaceRecord) -> dict:
    return {
        "id": record.id,
        "appointment_id": record.appointment_id,
        "status": record.status,
        "version_number": record.version_number,
        "payload": record.payload,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


@router.get("")
def list_appointment_workspaces(request: Request, db: Session = Depends(get_db)):
    person_id, _ = _scope(request, db)
    rows = db.query(AppointmentWorkspaceRecord).filter(
        AppointmentWorkspaceRecord.person_id == person_id
    ).order_by(AppointmentWorkspaceRecord.updated_at.desc()).limit(50).all()
    return {"items": [_record_out(row) for row in rows], "limit": 50}


@router.post("", status_code=201)
def create_appointment_workspace(payload: AppointmentWorkspaceCreate, request: Request, db: Session = Depends(get_db)):
    person_id, caregiver_id = _scope(request, db)
    if len(json.dumps(payload.payload, ensure_ascii=False, default=str)) > 100_000:
        raise HTTPException(status_code=413, detail="Appointment workspace payload exceeds 100 KB")
    if payload.appointment_id:
        appointment = db.query(Appointment).filter(
            Appointment.id == payload.appointment_id,
            Appointment.person_id == person_id,
        ).first()
        if appointment is None:
            raise HTTPException(status_code=404, detail="Appointment not found for this care record")
    row = AppointmentWorkspaceRecord(
        id=str(uuid.uuid4()),
        person_id=person_id,
        appointment_id=payload.appointment_id,
        created_by_caregiver_id=caregiver_id,
        updated_by_caregiver_id=caregiver_id,
        status=payload.status,
        version_number=1,
        payload=payload.payload,
        last_revision_reason=payload.change_reason,
    )
    db.add(row)
    db.flush()
    db.add(AppointmentWorkspaceRevision(
        id=str(uuid.uuid4()), record_id=row.id, person_id=person_id,
        revision_number=1, payload=payload.payload,
        change_reason=payload.change_reason, changed_by_caregiver_id=caregiver_id,
    ))
    try:
        db.commit()
        db.refresh(row)
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=503, detail="Appointment workspace could not be persisted") from exc
    return _record_out(row)



@router.get("/preparation-brief")
def get_appointment_preparation_brief(request: Request, window_days: int = 90, db: Session = Depends(get_db)):
    """Return a bounded, source-linked LCR preparation view without inferring clinical meaning."""
    if window_days < 1 or window_days > 365:
        raise HTTPException(status_code=422, detail="window_days must be between 1 and 365")
    person_id, _ = _scope(request, db)
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=window_days)

    try:
        fact_columns = {column["name"] for column in inspect(db.get_bind()).get_columns("care_facts")}
        fact_select = [
            name for name in (
                "id", "fact_type", "fact_value", "epistemic_status", "effective_at",
                "created_at", "updated_at", "evidence_ids", "source_evidence_id",
            ) if name in fact_columns
        ]
        if not {"id", "fact_type", "fact_value"}.issubset(fact_columns):
            raise RuntimeError("care_facts is missing required preparation fields")
        fact_rows = db.execute(text(
            "SELECT " + ", ".join(fact_select) + " FROM care_facts "
            "WHERE person_id = :person_id "
            "ORDER BY COALESCE(updated_at, created_at) DESC LIMIT 40"
        ), {"person_id": person_id}).mappings().all()
    except Exception:
        db.rollback()
        fact_rows = None

    events = db.query(CareEvent).filter(
        CareEvent.person_id == person_id,
        CareEvent.occurred_at >= cutoff,
    ).order_by(CareEvent.occurred_at.desc()).limit(30).all()
    appointments = db.query(Appointment).filter(
        Appointment.person_id == person_id,
    ).order_by(Appointment.scheduled_at.desc()).limit(15).all()

    facts = []
    if fact_rows is not None:
        for row in fact_rows:
            facts.append({
                "source_table": "care_facts",
                "source_id": str(row["id"]),
                "fact_type": row["fact_type"],
                "value": row["fact_value"],
                "epistemic_status": (row["epistemic_status"] or "unknown") if "epistemic_status" in row else "unknown",
                "effective_at": _iso(row["effective_at"]) if "effective_at" in row else None,
                "recorded_at": _iso(row["created_at"]) if "created_at" in row else None,
                "updated_at": _iso(row["updated_at"]) if "updated_at" in row else None,
                "evidence_ids": _evidence_ids_from_row(row),
            })

    event_items = [{
        "source_table": "care_events",
        "source_id": str(item.id),
        "event_type": item.event_type,
        "title": item.title,
        "description": item.description,
        "status": item.status,
        "occurred_at": item.occurred_at.isoformat() if item.occurred_at else None,
        "recorded_at": item.recorded_at.isoformat() if item.recorded_at else None,
        "evidence_ids": item.evidence_ids or [],
    } for item in events]
    appointment_items = [{
        "source_table": "appointments",
        "source_id": str(item.id),
        "title": item.title,
        "scheduled_at": item.scheduled_at.isoformat() if item.scheduled_at else None,
        "provider": item.provider,
        "healthcare_org": item.healthcare_org,
        "location": item.location,
        "status": item.status,
        "preparation_notes": item.preparation_notes,
        "evidence_ids": item.evidence_ids or [],
    } for item in appointments]

    # Attach provenance only for evidence IDs explicitly linked to this person's
    # preparation records. Author/uploader/reporter and event time stay separate.
    referenced_evidence_ids = set()
    for row in facts:
        referenced_evidence_ids.update(row.get("evidence_ids", []))
    for row in event_items + appointment_items:
        referenced_evidence_ids.update(
            str(value) for value in row.get("evidence_ids", []) if isinstance(value, str)
        )
    evidence_sources = {}
    evidence_rows = []
    if referenced_evidence_ids:
        evidence_rows = db.query(Evidence).filter(
            Evidence.person_id == person_id,
            Evidence.id.in_(sorted(referenced_evidence_ids)),
        ).all()
        evidence_sources = {
            row.id: {
                "evidence_id": row.id,
                "type": row.type,
                "evidence_kind": row.evidence_kind,
                "epistemic_status": row.epistemic_status or "unknown",
                "source_type": row.source_type,
                "source_text": (row.source_text or "")[:1200],
                "original_author": row.original_author,
                "reported_by_label": row.reported_by_label,
                "uploaded_by_caregiver_id": row.uploaded_by_caregiver_id,
                "reported_by_caregiver_id": row.reported_by_caregiver_id,
                "collected_by_caregiver_id": row.collected_by_caregiver_id,
                "occurred_at": _iso(row.occurred_at),
                "occurred_at_timezone": row.occurred_at_timezone,
                "collected_at": _iso(row.collected_at),
                "ingested_at": _iso(row.ingested_at),
                "original_source_reference": row.original_source_reference,
                "parent_evidence_id": row.parent_evidence_id,
                "source_text_sha256": row.source_text_sha256,
            }
            for row in evidence_rows
        }
    for row in facts + event_items + appointment_items:
        row["evidence_sources"] = [
            evidence_sources[evidence_id]
            for evidence_id in row.get("evidence_ids", [])
            if evidence_id in evidence_sources
        ]
        row["evidence_reference_status"] = (
            "linked_sources_available" if row["evidence_sources"]
            else "referenced_sources_unavailable" if row.get("evidence_ids")
            else "no_evidence_linked"
        )

    lineage_relationships = []
    if inspect(db.get_bind()).has_table("evidence_source_relationships") and referenced_evidence_ids:
        lineage_relationships = db.query(EvidenceSourceRelationship).filter(
            EvidenceSourceRelationship.person_id == person_id,
            (EvidenceSourceRelationship.source_evidence_id.in_(sorted(referenced_evidence_ids)))
            | (EvidenceSourceRelationship.related_evidence_id.in_(sorted(referenced_evidence_ids))),
        ).all()
    from app.services.evidence_lineage import summarize_evidence_lineage
    evidence_lineage = summarize_evidence_lineage(
        evidence_rows, lineage_relationships, evidence_sources.keys()
    )

    return {
        "person_scope_verified": True,
        "snapshot_at": now.isoformat(),
        "window_days": window_days,
        "read_only": True,
        "updates_lcr": False,
        "facts": facts,
        "events": event_items,
        "appointments": appointment_items,
        "evidence_lineage": evidence_lineage,
        "source_coverage": {
            "care_facts": "available" if fact_rows is not None else "unavailable",
            "care_events": "available" if event_items else "no_records_returned",
            "appointments": "available" if appointment_items else "no_records_returned",
            "medications_and_allergies": "unavailable",
            "documents": "unavailable",
            "community": "not_authorized",
        },
        "limitations": [
            "This is a source-linked preparation view, not a clinical assessment.",
            "Caregiver reports remain reports; no diagnosis, cause, medication instruction, or clinician finding is inferred.",
            "Missing or unavailable sources are not evidence that the information does not exist.",
        ],
    }


@router.get("/{record_id}")
def get_appointment_workspace(record_id: str, request: Request, db: Session = Depends(get_db)):
    person_id, _ = _scope(request, db)
    row = db.query(AppointmentWorkspaceRecord).filter(
        AppointmentWorkspaceRecord.id == record_id,
        AppointmentWorkspaceRecord.person_id == person_id,
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Appointment workspace not found")
    return _record_out(row)


@router.patch("/{record_id}")
def update_appointment_workspace(record_id: str, payload: AppointmentWorkspaceUpdate, request: Request, db: Session = Depends(get_db)):
    person_id, caregiver_id = _scope(request, db)
    if len(json.dumps(payload.payload, ensure_ascii=False, default=str)) > 100_000:
        raise HTTPException(status_code=413, detail="Appointment workspace payload exceeds 100 KB")
    row = db.query(AppointmentWorkspaceRecord).filter(
        AppointmentWorkspaceRecord.id == record_id,
        AppointmentWorkspaceRecord.person_id == person_id,
    ).with_for_update().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Appointment workspace not found")
    if row.version_number != payload.expected_version:
        raise HTTPException(status_code=409, detail={
            "code": "appointment_workspace_version_conflict",
            "expected_version": payload.expected_version,
            "current_version": row.version_number,
            "message": "This workspace changed elsewhere. Reload before saving to avoid overwriting newer notes.",
        })
    next_version = row.version_number + 1
    row.payload = payload.payload
    if payload.status is not None:
        row.status = payload.status
    row.version_number = next_version
    row.updated_by_caregiver_id = caregiver_id
    row.last_revision_reason = payload.change_reason
    db.add(AppointmentWorkspaceRevision(
        id=str(uuid.uuid4()), record_id=row.id, person_id=person_id,
        revision_number=next_version, payload=payload.payload,
        change_reason=payload.change_reason, changed_by_caregiver_id=caregiver_id,
    ))
    try:
        db.commit()
        db.refresh(row)
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=503, detail="Appointment workspace update could not be persisted") from exc
    return _record_out(row)


@router.get("/{record_id}/revisions")
def list_appointment_workspace_revisions(record_id: str, request: Request, db: Session = Depends(get_db)):
    person_id, _ = _scope(request, db)
    row = db.query(AppointmentWorkspaceRecord.id).filter(
        AppointmentWorkspaceRecord.id == record_id,
        AppointmentWorkspaceRecord.person_id == person_id,
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Appointment workspace not found")
    revisions = db.query(AppointmentWorkspaceRevision).filter(
        AppointmentWorkspaceRevision.record_id == record_id,
        AppointmentWorkspaceRevision.person_id == person_id,
    ).order_by(AppointmentWorkspaceRevision.revision_number.asc()).all()
    return {"items": [{
        "id": item.id, "record_id": item.record_id, "revision_number": item.revision_number,
        "payload": item.payload, "change_reason": item.change_reason,
        "changed_at": item.changed_at, "changed_by_caregiver_id": item.changed_by_caregiver_id,
    } for item in revisions]}
