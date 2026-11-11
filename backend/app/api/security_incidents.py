"""Restricted operator API for incident register and append-only timeline.

This router is intentionally unavailable unless a high-entropy operator token is
configured in the deployment secret store. It is not a caregiver-facing API.
"""
import hmac
import os
import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import MetaData, Table, select
from sqlalchemy.orm import Session

from app.core.database import get_db

router = APIRouter(prefix="/security/incidents", tags=["security incident operations"])

Severity = Literal["SEV-1", "SEV-2", "SEV-3", "SEV-4"]
IncidentStatus = Literal["open", "investigating", "contained", "notification_assessment", "recovering", "closed"]


def _operator(token: str | None = Header(default=None, alias="X-SolenOS-Incident-Admin-Token")) -> str:
    expected = os.getenv("SOLENOS_INCIDENT_ADMIN_TOKEN", "").strip()
    if len(expected) < 32:
        raise HTTPException(status_code=503, detail="Incident operator access is not configured")
    if not token or not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=401, detail="Invalid incident operator credentials")
    return "incident_operator"


def _table(db: Session, name: str) -> Table:
    try:
        return Table(name, MetaData(), autoload_with=db.get_bind())
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Incident register schema is unavailable: {name}") from exc


def _available_values(table: Table, values: dict) -> dict:
    return {key: value for key, value in values.items() if key in table.c}


class IncidentCreate(BaseModel):
    severity: Severity
    incident_type: str = Field(min_length=3, max_length=100)
    detected_at: datetime
    owner_id: str = Field(min_length=1, max_length=255)
    description_redacted: str = Field(min_length=10, max_length=2000)
    affected_systems: list[str] = Field(default_factory=list, max_length=20)


class IncidentEventCreate(BaseModel):
    event_type: str = Field(min_length=3, max_length=100)
    notes_redacted: str = Field(min_length=3, max_length=2000)


class IncidentStatusUpdate(BaseModel):
    incident_status: IncidentStatus
    notes_redacted: str = Field(min_length=3, max_length=2000)


@router.post("", status_code=201)
def create_incident(
    payload: IncidentCreate,
    actor: str = Depends(_operator),
    db: Session = Depends(get_db),
):
    incidents = _table(db, "privacy_incidents")
    events = _table(db, "privacy_incident_events")
    incident_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    detected_at = payload.detected_at
    if detected_at.tzinfo is None:
        raise HTTPException(status_code=422, detail="detected_at must include a timezone")
    incident_values = _available_values(incidents, {
        "id": incident_id,
        "incident_status": "open",
        "severity": payload.severity,
        "detected_at": detected_at,
        "incident_type": payload.incident_type.strip(),
        "owner_id": payload.owner_id.strip(),
        "actor_id": actor,
        "metadata": {
            "description_redacted": payload.description_redacted.strip(),
            "affected_systems": [item.strip()[:100] for item in payload.affected_systems if item.strip()],
        },
        "created_at": now,
        "updated_at": now,
    })
    event_values = _available_values(events, {
        "id": str(uuid.uuid4()),
        "incident_id": incident_id,
        "event_type": "incident_opened",
        "occurred_at": now,
        "actor_id": actor,
        "notes_redacted": "Incident created; initial details stored in restricted incident metadata.",
        "created_at": now,
        "updated_at": now,
    })
    try:
        db.execute(incidents.insert().values(**incident_values))
        db.execute(events.insert().values(**event_values))
        db.commit()
    except Exception:
        db.rollback()
        raise
    return {
        "id": incident_id,
        "incident_status": "open",
        "severity": payload.severity,
        "detected_at": detected_at.isoformat(),
        "created_at": now.isoformat(),
    }


@router.get("")
def list_incidents(
    actor: str = Depends(_operator),
    db: Session = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=100),
):
    incidents = _table(db, "privacy_incidents")
    columns = [incidents.c[name] for name in ("id", "incident_status", "severity", "detected_at", "closed_at", "incident_type", "owner_id") if name in incidents.c]
    rows = db.execute(select(*columns).order_by(incidents.c.detected_at.desc()).limit(limit)).mappings().all()
    return {"incidents": [dict(row) for row in rows]}


@router.get("/{incident_id}/events")
def list_incident_events(
    incident_id: str,
    actor: str = Depends(_operator),
    db: Session = Depends(get_db),
    limit: int = Query(default=100, ge=1, le=250),
):
    incidents = _table(db, "privacy_incidents")
    events = _table(db, "privacy_incident_events")
    exists = db.execute(select(incidents.c.id).where(incidents.c.id == incident_id)).first()
    if not exists:
        raise HTTPException(status_code=404, detail="Incident not found")
    columns = [events.c[name] for name in ("id", "incident_id", "event_type", "occurred_at", "actor_id", "notes_redacted") if name in events.c]
    rows = db.execute(
        select(*columns).where(events.c.incident_id == incident_id)
        .order_by(events.c.occurred_at.asc()).limit(limit)
    ).mappings().all()
    return {"events": [dict(row) for row in rows]}


@router.post("/{incident_id}/events", status_code=201)
def append_incident_event(
    incident_id: str,
    payload: IncidentEventCreate,
    actor: str = Depends(_operator),
    db: Session = Depends(get_db),
):
    incidents = _table(db, "privacy_incidents")
    events = _table(db, "privacy_incident_events")
    exists = db.execute(select(incidents.c.id).where(incidents.c.id == incident_id)).first()
    if not exists:
        raise HTTPException(status_code=404, detail="Incident not found")
    now = datetime.now(timezone.utc)
    values = _available_values(events, {
        "id": str(uuid.uuid4()),
        "incident_id": incident_id,
        "event_type": payload.event_type.strip(),
        "occurred_at": now,
        "actor_id": actor,
        "notes_redacted": payload.notes_redacted.strip(),
        "created_at": now,
        "updated_at": now,
    })
    db.execute(events.insert().values(**values))
    db.commit()
    return {"appended": True, "incident_id": incident_id, "occurred_at": now.isoformat()}


@router.patch("/{incident_id}/status")
def update_incident_status(
    incident_id: str,
    payload: IncidentStatusUpdate,
    actor: str = Depends(_operator),
    db: Session = Depends(get_db),
):
    incidents = _table(db, "privacy_incidents")
    events = _table(db, "privacy_incident_events")
    row = db.execute(select(incidents).where(incidents.c.id == incident_id)).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Incident not found")
    now = datetime.now(timezone.utc)
    updates = {"incident_status": payload.incident_status, "updated_at": now}
    if payload.incident_status == "closed" and "closed_at" in incidents.c:
        updates["closed_at"] = now
    if payload.incident_status != "closed" and "closed_at" in incidents.c:
        updates["closed_at"] = None
    db.execute(incidents.update().where(incidents.c.id == incident_id).values(**_available_values(incidents, updates)))
    event_values = _available_values(events, {
        "id": str(uuid.uuid4()),
        "incident_id": incident_id,
        "event_type": "status_changed",
        "occurred_at": now,
        "actor_id": actor,
        "notes_redacted": f"Status changed to {payload.incident_status}. {payload.notes_redacted.strip()}",
        "created_at": now,
        "updated_at": now,
    })
    db.execute(events.insert().values(**event_values))
    db.commit()
    return {"updated": True, "incident_id": incident_id, "incident_status": payload.incident_status}
