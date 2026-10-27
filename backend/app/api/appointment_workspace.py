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
from sqlalchemy import text

from app.core.database import get_db
from app.models.anonymous_session import AnonymousSession
from app.models.time_location import Appointment
from app.models.care import CareEvent
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
        fact_rows = db.execute(text("""
            SELECT id, fact_type, fact_value, epistemic_status, effective_at, created_at, updated_at
            FROM care_facts
            WHERE person_id = :person_id
            ORDER BY COALESCE(updated_at, created_at) DESC
            LIMIT 40
        """), {"person_id": person_id}).mappings().all()
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
                "epistemic_status": row["epistemic_status"] or "unknown",
                "effective_at": row["effective_at"].isoformat() if row["effective_at"] else None,
                "recorded_at": row["created_at"].isoformat() if row["created_at"] else None,
                "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
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

    return {
        "person_scope_verified": True,
        "snapshot_at": now.isoformat(),
        "window_days": window_days,
        "read_only": True,
        "updates_lcr": False,
        "facts": facts,
        "events": event_items,
        "appointments": appointment_items,
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
