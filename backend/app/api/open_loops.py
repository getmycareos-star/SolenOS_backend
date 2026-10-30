"""Persistent, evidence-backed open-loop tracking for caregiver continuity."""
import asyncio
import hashlib
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, Index, Integer, JSON, String, Text,
    UniqueConstraint, and_, or_, text as sql_text
)
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.core.database import get_db, SessionLocal
from app.models.anonymous_session import AnonymousSession
from app.models.open_loop import OpenLoopRecord, OpenLoopEvent, OpenLoopOccurrence, OpenLoopDependency

router = APIRouter(prefix="/open-loops", tags=["open-loops"])

LOOP_TYPES = {
    "unresolved_concern", "pending_decision", "pending_action", "follow_up",
    "unconfirmed_outcome", "missing_information", "awaiting_response",
    "professional_input", "contradiction", "deferred_decision",
}
OPEN_STATES = {"open", "waiting", "deferred", "blocked", "review_abandoned", "pending_closure_verification"}
TERMINAL_STATES = {"closed", "cancelled"}
ALL_STATES = OPEN_STATES | TERMINAL_STATES
OUTCOME_STATES = {"outcome_unknown", "outcome_reported", "outcome_verified"}
PRIORITY_ORDER = {"critical": 0, "high": 1, "normal": 2, "low": 3}


class LoopCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    loop_type: str
    title: str = Field(min_length=1, max_length=240)
    description: str = Field(min_length=1, max_length=12000)
    evidence: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    uncertainty: str = "unknown"
    source_type: Optional[str] = Field(default=None, max_length=80)
    source_id: Optional[str] = Field(default=None, max_length=160)
    owner_role: Optional[str] = Field(default=None, max_length=100)
    owner_label: Optional[str] = Field(default=None, max_length=200)
    deadline_at: Optional[datetime] = None
    deadline_source: Optional[str] = None
    deadline_timezone: Optional[str] = Field(default=None, max_length=80)
    consequence_of_delay: Optional[str] = None
    missing_information: list[str] = Field(default_factory=list, max_length=50)
    options: list[str] = Field(default_factory=list, max_length=50)
    request_text: Optional[str] = Field(default=None, max_length=12000)
    recipient_label: Optional[str] = Field(default=None, max_length=200)
    verification_rule: Optional[str] = Field(default=None, max_length=4000)
    deferred_reason: Optional[str] = Field(default=None, max_length=4000)
    review_at: Optional[datetime] = None
    idempotency_key: Optional[str] = Field(default=None, max_length=160)

    @field_validator("loop_type")
    @classmethod
    def valid_type(cls, value):
        if value not in LOOP_TYPES:
            raise ValueError("Unsupported open-loop type")
        return value

    @field_validator("deadline_source")
    @classmethod
    def valid_deadline_source(cls, value):
        if value is not None and value not in {"explicit_user", "recorded_commitment", "system_policy"}:
            raise ValueError("deadline_source must identify an actual supported source")
        return value


class LoopUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: Optional[str] = Field(default=None, min_length=1, max_length=240)
    description: Optional[str] = Field(default=None, min_length=1, max_length=12000)
    owner_role: Optional[str] = Field(default=None, max_length=100)
    owner_label: Optional[str] = Field(default=None, max_length=200)
    deadline_at: Optional[datetime] = None
    deadline_source: Optional[str] = None
    deadline_timezone: Optional[str] = Field(default=None, max_length=80)
    consequence_of_delay: Optional[str] = None
    missing_information: Optional[list[str]] = None
    options: Optional[list[str]] = None
    deferred_reason: Optional[str] = Field(default=None, max_length=4000)
    review_at: Optional[datetime] = None
    status: Optional[str] = None
    reason: Optional[str] = Field(default=None, max_length=4000)
    evidence: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    idempotency_key: Optional[str] = Field(default=None, max_length=160)


class EvidenceAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=12000)
    evidence: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    idempotency_key: Optional[str] = Field(default=None, max_length=160)
    verification_rule: Optional[str] = Field(default=None, max_length=4000)


class DependencyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prerequisite_id: str
    mandatory: bool = True


def _utc(value):
    if value is None:
        return None
    if value.tzinfo is None:
        raise HTTPException(status_code=422, detail="Timestamps must include a timezone")
    return value.astimezone(timezone.utc)




def _validate_source_evidence_references(db: Session, person_id: str, evidence: list[dict[str, Any]], *, required: bool = False) -> set[str]:
    """Validate cited evidence IDs against the authenticated person's persisted evidence.

    Descriptive report payloads may be recorded without a source ID, but they
    cannot verify an outcome or reconcile a contradiction.
    """
    from app.models.care import Evidence

    evidence_ids: set[str] = set()
    for item in evidence:
        if not isinstance(item, dict):
            raise HTTPException(status_code=422, detail="Evidence entries must be structured objects")
        if "evidence_id" not in item:
            continue
        raw_id = item.get("evidence_id")
        if not isinstance(raw_id, str) or not raw_id.strip():
            raise HTTPException(status_code=422, detail="evidence_id must be a non-empty persisted evidence identifier")
        evidence_ids.add(raw_id.strip())

    if required and not evidence_ids:
        raise HTTPException(
            status_code=422,
            detail="Verification requires at least one persisted source evidence ID from this care record; descriptive labels are not source evidence",
        )
    if evidence_ids:
        found = {
            str(row.id)
            for row in db.query(Evidence.id).filter(
                Evidence.person_id == str(person_id),
                Evidence.id.in_(sorted(evidence_ids)),
            ).all()
        }
        if found != evidence_ids:
            raise HTTPException(status_code=404, detail="Referenced source evidence was not found in this care record")
    return evidence_ids


def _scope(request: Request, db: Session) -> tuple[str, str]:
    authorization = request.headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(status_code=401, detail="A valid SolenOS session is required")
    digest = hashlib.sha256(token.strip().encode("utf-8")).hexdigest()
    session = db.query(AnonymousSession).filter(
        AnonymousSession.token_hash == digest,
        AnonymousSession.revoked_at.is_(None),
        AnonymousSession.expires_at > datetime.now(timezone.utc),
    ).first()
    if session is None:
        raise HTTPException(status_code=401, detail="Session is invalid or expired")
    return str(session.person_id), str(session.caregiver_id)


def _snapshot(loop):
    now = datetime.now(timezone.utc)
    def iso(value):
        return value.isoformat() if value is not None else None
    deadline = (loop.deadline_at.replace(tzinfo=timezone.utc) if loop.deadline_at and loop.deadline_at.tzinfo is None else _utc(loop.deadline_at) if loop.deadline_at else None)
    deps = getattr(loop, "_mandatory_blockers", [])
    return {
        "id": loop.id, "person_id": loop.person_id, "caregiver_id": loop.caregiver_id,
        "loop_type": loop.loop_type, "title": loop.title, "description": loop.description,
        "status": loop.status, "outcome_status": loop.outcome_status,
        "owner_role": loop.owner_role, "owner_label": loop.owner_label,
        "source_type": loop.source_type, "source_id": loop.source_id,
        "evidence": loop.evidence or [], "uncertainty": loop.uncertainty,
        "missing_information": loop.missing_information or [], "options": loop.options or [],
        "request_text": loop.request_text, "recipient_label": loop.recipient_label,
        "requested_at": iso(loop.requested_at), "last_contact_at": iso(loop.last_contact_at),
        "response_evidence": loop.response_evidence or [], "action_evidence": loop.action_evidence or [],
        "outcome_evidence": loop.outcome_evidence or [], "verification_rule": loop.verification_rule,
        "deadline_at": iso(deadline), "deadline_source": loop.deadline_source,
        "deadline_timezone": loop.deadline_timezone, "overdue": bool(deadline and deadline <= now and loop.status not in TERMINAL_STATES),
        "priority": loop.priority, "priority_reasons": loop.priority_reasons or [],
        "deferred_reason": loop.deferred_reason, "review_at": iso(loop.review_at),
        "closure_evidence": loop.closure_evidence or [], "closure_result": loop.closure_result,
        "closure_actor": loop.closure_actor, "closed_at": iso(loop.closed_at),
        "cancellation_reason": loop.cancellation_reason, "recurrence_of_id": loop.recurrence_of_id,
        "blocking_prerequisites": deps, "created_at": iso(loop.created_at), "updated_at": iso(loop.updated_at),
        "version": loop.version,
    }


def _audit(db, loop, event_type, before, evidence=None, key=None, actor="session_caregiver"):
    if key:
        existing = db.query(OpenLoopEvent).filter(
            OpenLoopEvent.person_id == loop.person_id,
            OpenLoopEvent.idempotency_key == key,
        ).first()
        if existing:
            # A reused key must abort the surrounding transaction. Returning
            # silently here allowed a retried request to commit a second state
            # mutation without a corresponding audit event.
            raise HTTPException(
                status_code=409,
                detail="Idempotency key has already been used for this care record",
            )
    db.add(OpenLoopEvent(
        loop_id=loop.id, person_id=loop.person_id, caregiver_id=loop.caregiver_id,
        event_type=event_type, before_state=before, after_state=_snapshot(loop),
        evidence=evidence or [], idempotency_key=key, actor=actor,
    ))
    return None


def _get_scoped(db, person_id, caregiver_id, loop_id, *, for_update=False):
    query = db.query(OpenLoopRecord).filter(
        OpenLoopRecord.id == loop_id,
        OpenLoopRecord.person_id == person_id,
        OpenLoopRecord.caregiver_id == caregiver_id,
    )
    # Mutations must serialize on the parent row before reading JSON-backed
    # evidence arrays. PostgreSQL's row lock makes concurrent append operations
    # reload the latest committed record instead of overwriting one another.
    if for_update and db.bind is not None and db.bind.dialect.name == "postgresql":
        query = query.with_for_update()
    loop = query.first()
    if loop is None:
        raise HTTPException(status_code=404, detail="Open loop not found")
    return loop


def _reassess(db, loop):
    reasons = []
    now = datetime.now(timezone.utc)
    deadline = loop.deadline_at
    if deadline and deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    if deadline and deadline <= now and loop.status not in TERMINAL_STATES:
        reasons.append("Persisted deadline has passed")
    if loop.loop_type == "contradiction" and loop.status not in TERMINAL_STATES:
        reasons.append("Competing claims remain unresolved")
    if loop.missing_information:
        reasons.append("Required information is still missing")
    if loop.status == "blocked":
        reasons.append("A mandatory prerequisite is unresolved")
    if loop.consequence_of_delay == "critical" and loop.status not in TERMINAL_STATES:
        reasons.append("Critical consequence of delay was explicitly recorded")
    if loop.status == "review_abandoned":
        reasons.append("Inactive item requires human review; this is not closure")
    if reasons and ("Persisted deadline has passed" in reasons or loop.consequence_of_delay == "critical"):
        loop.priority = "high"
    elif reasons:
        loop.priority = "normal"
    else:
        loop.priority = "low" if loop.status in TERMINAL_STATES else "normal"
    loop.priority_reasons = reasons
    loop.updated_at = now
    # SQLAlchemy column defaults are applied at INSERT/flush time, so a new
    # transient ORM object can still have version=None during its first pass.
    # Initialize the first persisted version deterministically.
    loop.version = (loop.version or 0) + 1
    return reasons


def _replay_event(db: Session, person_id: str, caregiver_id: str, loop_id: str,
                  event_type: str, idempotency_key: Optional[str]):
    """Resolve committed mutation retries without appending duplicate evidence."""
    if not idempotency_key:
        return None
    event = db.query(OpenLoopEvent).filter(
        OpenLoopEvent.person_id == person_id,
        OpenLoopEvent.idempotency_key == idempotency_key,
    ).first()
    if event is None:
        return None
    if event.loop_id != loop_id or event.event_type != event_type:
        raise HTTPException(status_code=409, detail="Idempotency key was already used for a different Open Loop event")
    return _snapshot(_get_scoped(db, person_id, caregiver_id, loop_id))


@router.post("", status_code=201)
def create_loop(payload: LoopCreate, request: Request, db: Session = Depends(get_db)):
    person_id, caregiver_id = _scope(request, db)
    _validate_source_evidence_references(db, person_id, payload.evidence)
    deadline = _utc(payload.deadline_at)
    if deadline and not payload.deadline_source:
        raise HTTPException(status_code=422, detail="A deadline requires an explicit source")
    if payload.deadline_source and not deadline:
        raise HTTPException(status_code=422, detail="A deadline source requires a deadline")
    if payload.loop_type == "deferred_decision" and not payload.deferred_reason:
        raise HTTPException(status_code=422, detail="A deferred decision requires its recorded reason")
    canonical = json.dumps({"type": payload.loop_type, "title": payload.title.strip().casefold(),
                            "description": payload.description.strip().casefold(),
                            "source_type": payload.source_type},
                           sort_keys=True, separators=(",", ":"))
    fingerprint = hashlib.sha256(canonical.encode()).hexdigest()
    if payload.idempotency_key and db.bind is not None and db.bind.dialect.name == "postgresql":
        # Serialize same-key creates per care record before any lookup or write.
        # The transaction-scoped lock makes concurrent retries observe the
        # committed winner under PostgreSQL READ COMMITTED isolation.
        lock_key = f"open-loop-create:{person_id}:{payload.idempotency_key}"
        db.execute(
            sql_text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
            {"lock_key": lock_key},
        )
    if payload.idempotency_key:
        replay = db.query(OpenLoopOccurrence).filter(
            OpenLoopOccurrence.person_id == person_id,
            OpenLoopOccurrence.idempotency_key == payload.idempotency_key,
        ).first()
        if replay:
            if replay.source_fingerprint != fingerprint:
                raise HTTPException(status_code=409, detail="Idempotency key was already used for a different Open Loop payload")
            return _snapshot(_get_scoped(db, person_id, caregiver_id, replay.loop_id))
    if payload.source_id:
        replay = db.query(OpenLoopOccurrence).join(
            OpenLoopRecord, OpenLoopOccurrence.loop_id == OpenLoopRecord.id
        ).filter(
            OpenLoopOccurrence.person_id == person_id,
            OpenLoopOccurrence.source_type == payload.source_type,
            OpenLoopOccurrence.source_id == payload.source_id,
            OpenLoopRecord.loop_type == payload.loop_type,
        ).first()
        if replay:
            return _snapshot(_get_scoped(db, person_id, caregiver_id, replay.loop_id))
    recurrence = db.query(OpenLoopRecord).filter(
        OpenLoopRecord.person_id == person_id,
        OpenLoopRecord.caregiver_id == caregiver_id,
        OpenLoopRecord.loop_type == payload.loop_type,
        OpenLoopRecord.source_fingerprint == fingerprint,
        OpenLoopRecord.status.notin_(TERMINAL_STATES),
    ).order_by(OpenLoopRecord.created_at.asc()).first()
    if recurrence:
        occurrence = OpenLoopOccurrence(
            loop_id=recurrence.id, person_id=person_id, caregiver_id=caregiver_id,
            source_type=payload.source_type, source_id=payload.source_id,
            source_fingerprint=fingerprint, evidence=payload.evidence,
            idempotency_key=payload.idempotency_key,
        )
        db.add(occurrence)
        before = _snapshot(recurrence)
        _audit(db, recurrence, "recurrence_linked", before, payload.evidence, payload.idempotency_key)
        db.commit()
        return {**_snapshot(recurrence), "recurrence_detected": True, "occurrence_id": occurrence.id}
    loop = OpenLoopRecord(
        person_id=person_id, caregiver_id=caregiver_id, loop_type=payload.loop_type,
        title=payload.title.strip(), description=payload.description.strip(), evidence=payload.evidence,
        uncertainty=payload.uncertainty, source_type=payload.source_type, source_id=payload.source_id,
        source_fingerprint=fingerprint, owner_role=payload.owner_role, owner_label=payload.owner_label,
        deadline_at=deadline, deadline_source=payload.deadline_source, deadline_timezone=payload.deadline_timezone,
        consequence_of_delay=payload.consequence_of_delay, missing_information=payload.missing_information,
        options=payload.options, request_text=payload.request_text, recipient_label=payload.recipient_label,
        verification_rule=payload.verification_rule, deferred_reason=payload.deferred_reason,
        review_at=_utc(payload.review_at), status="deferred" if payload.loop_type == "deferred_decision" else "open",
    )
    if payload.loop_type == "awaiting_response":
        loop.requested_at = datetime.now(timezone.utc)
        loop.status = "waiting"
    _reassess(db, loop)
    db.add(loop)
    db.flush()
    occurrence = OpenLoopOccurrence(
        loop_id=loop.id, person_id=person_id, caregiver_id=caregiver_id,
        source_type=payload.source_type, source_id=payload.source_id,
        source_fingerprint=fingerprint, evidence=payload.evidence,
        idempotency_key=payload.idempotency_key,
    )
    db.add(occurrence)
    try:
        _audit(db, loop, "created", None, payload.evidence, payload.idempotency_key)
    except HTTPException as exc:
        # A concurrent identical create can pass the first occurrence lookup,
        # then observe the winning request's audit row before its own commit.
        # _audit correctly refuses a duplicate audit mutation; recover the
        # already committed occurrence instead of leaking a false 409.
        db.rollback()
        if (
            payload.idempotency_key
            and exc.status_code == 409
            and exc.detail == "Idempotency key has already been used for this care record"
        ):
            replay = db.query(OpenLoopOccurrence).filter(
                OpenLoopOccurrence.person_id == person_id,
                OpenLoopOccurrence.idempotency_key == payload.idempotency_key,
            ).first()
            if replay is not None:
                if replay.source_fingerprint != fingerprint:
                    raise HTTPException(
                        status_code=409,
                        detail="Idempotency key was already used for a different Open Loop payload",
                    )
                existing = _get_scoped(db, person_id, caregiver_id, replay.loop_id)
                return {
                    **_snapshot(existing),
                    "recurrence_detected": True,
                    "occurrence_id": replay.id,
                }
        raise
    try:
        db.commit()
    except IntegrityError:
        # Concurrent retries can both pass the initial lookup. The unique
        # occurrence constraint is the database arbiter; after rollback, return
        # the already committed record for the same idempotency/source key.
        db.rollback()
        replay_query = db.query(OpenLoopOccurrence).filter(
            OpenLoopOccurrence.person_id == person_id,
        )
        if payload.idempotency_key:
            replay_query = replay_query.filter(
                OpenLoopOccurrence.idempotency_key == payload.idempotency_key,
            )
        elif payload.source_id:
            replay_query = replay_query.filter(
                OpenLoopOccurrence.source_type == payload.source_type,
                OpenLoopOccurrence.source_id == payload.source_id,
            )
        else:
            raise HTTPException(status_code=409, detail="Open Loop creation conflicted with a concurrent write")
        replay = replay_query.first()
        if replay is None:
            raise HTTPException(status_code=409, detail="Open Loop creation conflicted with a concurrent write")
        if payload.idempotency_key and replay.source_fingerprint != fingerprint:
            raise HTTPException(status_code=409, detail="Idempotency key was already used for a different Open Loop payload")
        existing = _get_scoped(db, person_id, caregiver_id, replay.loop_id)
        return {**_snapshot(existing), "recurrence_detected": True, "occurrence_id": replay.id}
    db.refresh(loop)
    return {**_snapshot(loop), "recurrence_detected": False, "occurrence_id": occurrence.id}


@router.get("")
def list_loops(request: Request, status: Optional[str] = None, loop_type: Optional[str] = None,
               priority: Optional[str] = None, overdue: Optional[bool] = None,
               limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0),
               db: Session = Depends(get_db)):
    person_id, caregiver_id = _scope(request, db)
    query = db.query(OpenLoopRecord).filter(
        OpenLoopRecord.person_id == person_id, OpenLoopRecord.caregiver_id == caregiver_id)
    if status:
        if status not in ALL_STATES: raise HTTPException(status_code=422, detail="Invalid status filter")
        query = query.filter(OpenLoopRecord.status == status)
    if loop_type:
        if loop_type not in LOOP_TYPES: raise HTTPException(status_code=422, detail="Invalid loop type filter")
        query = query.filter(OpenLoopRecord.loop_type == loop_type)
    if priority:
        query = query.filter(OpenLoopRecord.priority == priority)
    now = datetime.now(timezone.utc)
    if overdue is True:
        query = query.filter(
            OpenLoopRecord.deadline_at.is_not(None),
            OpenLoopRecord.deadline_at <= now,
            OpenLoopRecord.status.notin_(TERMINAL_STATES),
        )
    elif overdue is False:
        query = query.filter(
            or_(
                OpenLoopRecord.deadline_at.is_(None),
                OpenLoopRecord.deadline_at > now,
                OpenLoopRecord.status.in_(TERMINAL_STATES),
            )
        )
    total = query.count()
    rows = query.order_by(
        OpenLoopRecord.deadline_at.asc().nullslast(),
        OpenLoopRecord.updated_at.desc(),
    ).offset(offset).limit(limit).all()
    items = []
    for loop in rows:
        deps = db.query(OpenLoopDependency).filter(
            OpenLoopDependency.dependent_id == loop.id, OpenLoopDependency.removed_at.is_(None),
            OpenLoopDependency.mandatory.is_(True)).all()
        blockers = []
        for dep in deps:
            prerequisite = db.query(OpenLoopRecord).filter(
                OpenLoopRecord.id == dep.prerequisite_id, OpenLoopRecord.person_id == person_id,
                OpenLoopRecord.caregiver_id == caregiver_id).first()
            if prerequisite and prerequisite.status != "closed":
                blockers.append({"id": prerequisite.id, "title": prerequisite.title, "status": prerequisite.status})
        loop._mandatory_blockers = blockers
        items.append(_snapshot(loop))
    return {"items": items, "limit": limit, "offset": offset, "count": total}


@router.get("/{loop_id}")
def get_loop(loop_id: str, request: Request, db: Session = Depends(get_db)):
    person_id, caregiver_id = _scope(request, db)
    loop = _get_scoped(db, person_id, caregiver_id, loop_id)
    deps = db.query(OpenLoopDependency).filter(
        OpenLoopDependency.dependent_id == loop.id, OpenLoopDependency.removed_at.is_(None)).all()
    blockers = []
    for dep in deps:
        prerequisite = _get_scoped(db, person_id, caregiver_id, dep.prerequisite_id)
        if dep.mandatory and prerequisite.status != "closed":
            blockers.append({"id": prerequisite.id, "title": prerequisite.title, "status": prerequisite.status})
    loop._mandatory_blockers = blockers
    return {"loop": _snapshot(loop), "history": [
        {"id": e.id, "event_type": e.event_type, "before_state": e.before_state, "after_state": e.after_state,
         "evidence": e.evidence, "actor": e.actor, "created_at": e.created_at}
        for e in db.query(OpenLoopEvent).filter(OpenLoopEvent.loop_id == loop.id).order_by(OpenLoopEvent.created_at.asc()).limit(500).all()
    ], "dependencies": [{"prerequisite_id": d.prerequisite_id, "mandatory": d.mandatory} for d in deps]}


@router.patch("/{loop_id}")
def update_loop(loop_id: str, payload: LoopUpdate, request: Request, db: Session = Depends(get_db)):
    person_id, caregiver_id = _scope(request, db)
    loop = _get_scoped(db, person_id, caregiver_id, loop_id, for_update=True)
    replay = _replay_event(db, person_id, caregiver_id, loop_id, "updated", payload.idempotency_key)
    if replay is not None:
        return replay
    before = _snapshot(loop)
    changes = payload.model_dump(exclude_unset=True)
    evidence = changes.pop("evidence", [])
    _validate_source_evidence_references(db, person_id, evidence)
    key = changes.pop("idempotency_key", None)
    reason = changes.pop("reason", None)
    requested_status = changes.pop("status", None)
    if requested_status:
        if requested_status not in ALL_STATES: raise HTTPException(status_code=422, detail="Invalid lifecycle status")
        if loop.status in TERMINAL_STATES:
            raise HTTPException(status_code=409, detail="Terminal loops must use the explicit reopening endpoint")
        if requested_status in {"closed", "cancelled"}:
            raise HTTPException(status_code=409, detail="Use the closure-verification or cancellation endpoint")
        if requested_status == "deferred" and not (changes.get("deferred_reason") or loop.deferred_reason):
            raise HTTPException(status_code=422, detail="Deferral requires an auditable reason")
        loop.status = requested_status
    if "deadline_at" in changes:
        deadline = _utc(changes["deadline_at"])
        if deadline and not (changes.get("deadline_source") or loop.deadline_source):
            raise HTTPException(status_code=422, detail="A deadline requires an explicit source")
        loop.deadline_at = deadline
    if changes.get("deadline_source") is not None and changes["deadline_source"] not in {"explicit_user", "recorded_commitment", "system_policy"}:
        raise HTTPException(status_code=422, detail="Invalid deadline source")
    for field, value in changes.items():
        if field != "deadline_at":
            setattr(loop, field, value)
    if requested_status == "deferred" and not reason and not loop.deferred_reason:
        raise HTTPException(status_code=422, detail="Deferral reason is required")
    if requested_status == "deferred" and reason:
        loop.deferred_reason = reason
    if requested_status == "review_abandoned" and not reason:
        raise HTTPException(status_code=422, detail="Abandonment review requires a reason")
    if requested_status == "review_abandoned":
        loop.inactive_since = loop.updated_at
    if reason:
        evidence = [*evidence, {"kind": "human_reason", "text": reason}]
    _reassess(db, loop)
    _audit(db, loop, "updated", before, evidence, key)
    db.commit()
    db.refresh(loop)
    return _snapshot(loop)


@router.post("/{loop_id}/responses")
def record_response(loop_id: str, payload: EvidenceAction, request: Request, db: Session = Depends(get_db)):
    person_id, caregiver_id = _scope(request, db)
    loop = _get_scoped(db, person_id, caregiver_id, loop_id, for_update=True)
    replay = _replay_event(db, person_id, caregiver_id, loop_id, "response_recorded", payload.idempotency_key)
    if replay is not None:
        return replay
    if loop.loop_type not in {"awaiting_response", "professional_input", "follow_up"}:
        raise HTTPException(status_code=409, detail="This loop type does not accept a response event")
    _validate_source_evidence_references(db, person_id, payload.evidence, required=loop.loop_type == "professional_input")
    if loop.loop_type == "professional_input":
        # Import lazily: database.py registers the Open Loop ORM during startup,
        # while app.models.care imports database.Base. A module-level import here
        # creates a care -> database -> open_loops -> care circular import.
        from app.models.care import Evidence

        if not payload.evidence:
            raise HTTPException(status_code=422, detail="Professional input requires source evidence; caregiver text alone cannot establish it")
        evidence_ids = {
            str(item.get("evidence_id", "")).strip()
            for item in payload.evidence
            if isinstance(item, dict) and str(item.get("evidence_id", "")).strip()
        }
        if not evidence_ids:
            raise HTTPException(
                status_code=422,
                detail="Professional input must reference an existing care-record evidence ID; caregiver notes alone are not source evidence",
            )
        verified_ids = {
            row.id for row in db.query(Evidence).filter(
                Evidence.person_id == person_id,
                Evidence.id.in_(evidence_ids),
            ).all()
        }
        if verified_ids != evidence_ids:
            # Do not reveal whether an unrecognized evidence ID belongs to another person.
            raise HTTPException(status_code=404, detail="Referenced source evidence was not found in this care record")
    before = _snapshot(loop)
    loop.response_evidence = [*(loop.response_evidence or []), {"text": payload.text, "evidence": payload.evidence, "recorded_at": datetime.now(timezone.utc).isoformat()}]
    loop.last_contact_at = datetime.now(timezone.utc)
    if loop.loop_type == "professional_input" and not payload.evidence:
        loop.status = "waiting"
    else:
        loop.status = "open"
    _reassess(db, loop)
    _audit(db, loop, "response_recorded", before, payload.evidence, payload.idempotency_key)
    db.commit()
    db.refresh(loop)
    return _snapshot(loop)


@router.post("/{loop_id}/actions")
def record_action(loop_id: str, payload: EvidenceAction, request: Request, db: Session = Depends(get_db)):
    person_id, caregiver_id = _scope(request, db)
    loop = _get_scoped(db, person_id, caregiver_id, loop_id, for_update=True)
    replay = _replay_event(db, person_id, caregiver_id, loop_id, "action_recorded", payload.idempotency_key)
    if replay is not None:
        return replay
    _validate_source_evidence_references(db, person_id, payload.evidence)
    before = _snapshot(loop)
    loop.action_evidence = [*(loop.action_evidence or []), {"text": payload.text, "evidence": payload.evidence, "recorded_at": datetime.now(timezone.utc).isoformat()}]
    _reassess(db, loop)
    _audit(db, loop, "action_recorded", before, payload.evidence, payload.idempotency_key)
    try:
        db.commit()
    except IntegrityError:
        # Another request may commit this idempotency key after the first replay
        # lookup. Roll back the losing mutation and return the committed winner.
        db.rollback()
        if payload.idempotency_key:
            replay = _replay_event(
                db, person_id, caregiver_id, loop_id,
                "action_recorded", payload.idempotency_key,
            )
            if replay is not None:
                return replay
        raise HTTPException(
            status_code=409,
            detail="Concurrent Open Loop mutation conflicted; retry with the same idempotency key",
        )
    db.refresh(loop)
    return _snapshot(loop)


@router.post("/{loop_id}/reconcile")
def reconcile_contradiction(loop_id: str, payload: EvidenceAction, request: Request, db: Session = Depends(get_db)):
    person_id, caregiver_id = _scope(request, db)
    loop = _get_scoped(db, person_id, caregiver_id, loop_id, for_update=True)
    replay = _replay_event(db, person_id, caregiver_id, loop_id, "contradiction_reconciled", payload.idempotency_key)
    if replay is not None:
        return replay
    if loop.loop_type != "contradiction":
        raise HTTPException(status_code=409, detail="Only contradiction loops can be reconciled")
    if not payload.evidence:
        raise HTTPException(status_code=422, detail="Reconciliation requires supporting evidence")
    _validate_source_evidence_references(db, person_id, payload.evidence, required=True)
    before = _snapshot(loop)
    reconciliation = {
        "kind": "explicit_reconciliation",
        "decision": payload.text,
        "evidence": payload.evidence,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "verification_basis": "caregiver_attestation_with_persisted_source_link",
        "semantic_grounding_status": "not_automatically_verified",
        "interpretation_limit": "Persisted source linkage and caregiver review do not automatically establish semantic entailment or prove which account is true.",
    }
    loop.outcome_evidence = [*(loop.outcome_evidence or []), reconciliation]
    loop.outcome_status = "outcome_reported"
    _audit(db, loop, "contradiction_reconciled", before, payload.evidence, payload.idempotency_key)
    db.commit()
    db.refresh(loop)
    return _snapshot(loop)

@router.post("/{loop_id}/outcomes")
def record_outcome(loop_id: str, payload: EvidenceAction, request: Request, db: Session = Depends(get_db)):
    person_id, caregiver_id = _scope(request, db)
    loop = _get_scoped(db, person_id, caregiver_id, loop_id, for_update=True)
    replay = _replay_event(db, person_id, caregiver_id, loop_id, "outcome_reported", payload.idempotency_key)
    if replay is not None:
        return replay
    _validate_source_evidence_references(db, person_id, payload.evidence)
    before = _snapshot(loop)
    loop.outcome_evidence = [*(loop.outcome_evidence or []), {"text": payload.text, "evidence": payload.evidence, "recorded_at": datetime.now(timezone.utc).isoformat()}]
    loop.outcome_status = "outcome_reported"
    _audit(db, loop, "outcome_reported", before, payload.evidence, payload.idempotency_key)
    db.commit()
    db.refresh(loop)
    return _snapshot(loop)


@router.post("/{loop_id}/verify-outcome")
def verify_outcome(loop_id: str, payload: EvidenceAction, request: Request, db: Session = Depends(get_db)):
    person_id, caregiver_id = _scope(request, db)
    loop = _get_scoped(db, person_id, caregiver_id, loop_id, for_update=True)
    replay = _replay_event(db, person_id, caregiver_id, loop_id, "outcome_attested", payload.idempotency_key)
    if replay is not None:
        return replay
    if not payload.evidence or not (loop.verification_rule or payload.verification_rule):
        raise HTTPException(status_code=409, detail="Outcome verification requires source evidence and an explicit verification rule")
    _validate_source_evidence_references(db, person_id, payload.evidence, required=True)
    before = _snapshot(loop)
    loop.verification_rule = loop.verification_rule or payload.verification_rule
    loop.outcome_evidence = [*(loop.outcome_evidence or []), {
        "text": payload.text,
        "evidence": payload.evidence,
        "verification_rule": loop.verification_rule,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "verification_basis": "caregiver_attestation_with_persisted_source_link",
        "semantic_grounding_status": "not_automatically_verified",
        "interpretation_limit": "Persisted source linkage verifies record existence and scope; it does not automatically establish semantic entailment, prove which account is true, or establish that an action caused the outcome.",
    }]
    loop.outcome_status = "outcome_reported"
    _audit(db, loop, "outcome_attested", before, payload.evidence, payload.idempotency_key)
    db.commit()
    db.refresh(loop)
    return _snapshot(loop)


@router.post("/{loop_id}/corroborate-outcome")
def corroborate_outcome(loop_id: str, payload: EvidenceAction, request: Request, db: Session = Depends(get_db)):
    """Record a caregiver-reviewed independent source relationship for an outcome.

    This verifies that an explicit, same-person, pair-level lineage assessment exists.
    It does not infer semantic entailment, truth, or causation from source linkage.
    """
    person_id, caregiver_id = _scope(request, db)
    loop = _get_scoped(db, person_id, caregiver_id, loop_id, for_update=True)
    replay = _replay_event(db, person_id, caregiver_id, loop_id, "outcome_corroborated", payload.idempotency_key)
    if replay is not None:
        return replay
    if not payload.verification_rule and not loop.verification_rule:
        raise HTTPException(status_code=409, detail="Outcome corroboration requires an explicit verification rule")
    evidence_ids = _validate_source_evidence_references(db, person_id, payload.evidence, required=True)
    if len(evidence_ids) != 2:
        raise HTTPException(status_code=422, detail="Independent corroboration requires exactly two distinct persisted source evidence IDs")
    from app.models.care_continuity import EvidenceSourceRelationship
    a, b = sorted(evidence_ids)
    relationship = db.query(EvidenceSourceRelationship).filter(
        EvidenceSourceRelationship.person_id == person_id,
        EvidenceSourceRelationship.relationship_type == "corroborates",
        EvidenceSourceRelationship.source_independence_status == "independent",
        EvidenceSourceRelationship.scope_type == "evidence_pair",
        EvidenceSourceRelationship.scope_id == "",
        EvidenceSourceRelationship.origin_group_key.is_(None),
        or_(
            and_(EvidenceSourceRelationship.source_evidence_id == a, EvidenceSourceRelationship.related_evidence_id == b),
            and_(EvidenceSourceRelationship.source_evidence_id == b, EvidenceSourceRelationship.related_evidence_id == a),
        ),
    ).first()
    if relationship is None:
        raise HTTPException(status_code=409, detail="No caregiver-reviewed independent corroboration relationship exists for these two sources")
    before = _snapshot(loop)
    loop.verification_rule = loop.verification_rule or payload.verification_rule
    loop.outcome_evidence = [*(loop.outcome_evidence or []), {
        "kind": "independent_source_corroboration_assessment",
        "text": payload.text,
        "evidence": payload.evidence,
        "verification_rule": loop.verification_rule,
        "source_relationship_id": relationship.id,
        "source_independence_status": relationship.source_independence_status,
        "assessment_basis": "persisted_caregiver_reviewed_independent_evidence_pair",
        "semantic_grounding_status": "not_automatically_verified",
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "interpretation_limit": "The care record contains a caregiver-reviewed independent-source relationship for this pair. This does not automatically establish that either source semantically entails the outcome, that either source is true, or that an action caused the outcome.",
    }]
    loop.outcome_status = "outcome_verified"
    _audit(db, loop, "outcome_corroborated", before, payload.evidence, payload.idempotency_key)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        replay = _replay_event(db, person_id, caregiver_id, loop_id, "outcome_corroborated", payload.idempotency_key)
        if replay is not None:
            return replay
        raise HTTPException(status_code=409, detail="Concurrent outcome corroboration conflicted; retry with the same idempotency key")
    db.refresh(loop)
    return _snapshot(loop)


@router.post("/{loop_id}/close")
def close_loop(loop_id: str, payload: EvidenceAction, request: Request, db: Session = Depends(get_db)):
    person_id, caregiver_id = _scope(request, db)
    loop = _get_scoped(db, person_id, caregiver_id, loop_id, for_update=True)
    replay = _replay_event(db, person_id, caregiver_id, loop_id, "closure_verified", payload.idempotency_key)
    if replay is not None:
        return {"accepted": True, "verification": "verified", "loop": replay}
    if loop.status in TERMINAL_STATES:
        raise HTTPException(status_code=409, detail="Loop is already terminal")
    blockers = db.query(OpenLoopDependency).filter(
        OpenLoopDependency.dependent_id == loop.id, OpenLoopDependency.removed_at.is_(None),
        OpenLoopDependency.mandatory.is_(True)).all()
    for dep in blockers:
        prerequisite = _get_scoped(db, person_id, caregiver_id, dep.prerequisite_id)
        if prerequisite.status != "closed":
            raise HTTPException(status_code=409, detail="A mandatory prerequisite must be verified closed; cancellation does not satisfy dependencies")
    if not payload.evidence:
        raise HTTPException(status_code=409, detail="Closure requires evidence")
    _validate_source_evidence_references(db, person_id, payload.evidence)
    if loop.loop_type in {"awaiting_response", "professional_input"} and not loop.response_evidence:
        raise HTTPException(status_code=409, detail="A response must be recorded before closure")
    if loop.loop_type == "professional_input" and not any(
        isinstance(item, dict) and any(
            isinstance(source, dict) and source.get("evidence_id")
            for source in (item.get("evidence") or [])
        )
        for item in (loop.response_evidence or [])
    ):
        raise HTTPException(status_code=409, detail="Professional input has not been substantiated by an existing care-record source")
    if loop.loop_type == "missing_information" and (loop.missing_information or loop.outcome_status != "outcome_verified"):
        raise HTTPException(status_code=409, detail="Required information must be obtained, validated, and recorded as a verified outcome")
    has_recorded_reconciliation = any(
        isinstance(item, dict) and item.get("kind") == "explicit_reconciliation"
        for item in (loop.outcome_evidence or [])
    )
    if loop.loop_type == "contradiction" and (
        loop.outcome_status != "outcome_verified" or not has_recorded_reconciliation
    ):
        raise HTTPException(
            status_code=409,
            detail="Contradiction closure requires a previously recorded, verified reconciliation and closure evidence",
        )
    if loop.outcome_status != "outcome_verified" and loop.loop_type in {"pending_action", "pending_decision", "deferred_decision", "unconfirmed_outcome", "follow_up", "unresolved_concern", "missing_information", "contradiction"}:
        raise HTTPException(status_code=409, detail="This loop requires a verified outcome before closure")
    before = _snapshot(loop)
    loop.status = "closed"
    loop.closure_evidence = payload.evidence
    loop.closure_result = "verified"
    loop.closure_actor = "session_caregiver"
    loop.closed_at = datetime.now(timezone.utc)

    # A mandatory dependent becomes actionable only when every prerequisite
    # reaches verified "closed". Cancellation deliberately does not unblock it.
    dependent_edges = db.query(OpenLoopDependency).filter(
        OpenLoopDependency.prerequisite_id == loop.id,
        OpenLoopDependency.removed_at.is_(None),
        OpenLoopDependency.mandatory.is_(True),
    ).all()
    for edge in dependent_edges:
        dependent = _get_scoped(db, person_id, caregiver_id, edge.dependent_id)
        if dependent.status in TERMINAL_STATES:
            continue
        active_edges = db.query(OpenLoopDependency).filter(
            OpenLoopDependency.dependent_id == dependent.id,
            OpenLoopDependency.removed_at.is_(None),
            OpenLoopDependency.mandatory.is_(True),
        ).all()
        all_verified_closed = all(
            _get_scoped(db, person_id, caregiver_id, active_edge.prerequisite_id).status == "closed"
            for active_edge in active_edges
        )
        if all_verified_closed and dependent.status == "blocked":
            dependent_before = _snapshot(dependent)
            dependent.status = "open"
            _reassess(db, dependent)
            _audit(db, dependent, "dependency_unblocked", dependent_before, [
                {"verified_closed_prerequisite_id": loop.id}
            ])

    _audit(db, loop, "closure_verified", before, payload.evidence, payload.idempotency_key)
    db.commit()
    db.refresh(loop)
    return {"accepted": True, "verification": "verified", "loop": _snapshot(loop)}


@router.post("/{loop_id}/cancel")
def cancel_loop(loop_id: str, payload: EvidenceAction, request: Request, db: Session = Depends(get_db)):
    person_id, caregiver_id = _scope(request, db)
    loop = _get_scoped(db, person_id, caregiver_id, loop_id, for_update=True)
    replay = _replay_event(db, person_id, caregiver_id, loop_id, "cancelled", payload.idempotency_key)
    if replay is not None:
        return {"accepted": True, "verification": "cancelled_not_resolved", "loop": replay}
    if loop.status in TERMINAL_STATES:
        raise HTTPException(status_code=409, detail="Loop is already terminal")
    before = _snapshot(loop)
    loop.status = "cancelled"
    loop.cancellation_reason = payload.text
    loop.closure_result = "cancelled_not_resolved"
    loop.closure_actor = "session_caregiver"
    loop.closed_at = datetime.now(timezone.utc)
    _audit(db, loop, "cancelled", before, payload.evidence, payload.idempotency_key)
    db.commit()
    db.refresh(loop)
    return {"accepted": True, "verification": "cancelled_not_resolved", "loop": _snapshot(loop)}


@router.post("/{loop_id}/reopen")
def reopen_loop(loop_id: str, payload: EvidenceAction, request: Request, db: Session = Depends(get_db)):
    person_id, caregiver_id = _scope(request, db)
    loop = _get_scoped(db, person_id, caregiver_id, loop_id, for_update=True)
    replay = _replay_event(db, person_id, caregiver_id, loop_id, "reopened", payload.idempotency_key)
    if replay is not None:
        return replay
    if loop.status not in TERMINAL_STATES:
        raise HTTPException(status_code=409, detail="Only closed or cancelled loops can be reopened")
    before = _snapshot(loop)
    loop.status = "open"
    loop.closed_at = None
    loop.closure_result = "reopened"
    loop.closure_evidence = [*(loop.closure_evidence or []), {"reopen_reason": payload.text, "evidence": payload.evidence}]
    _reassess(db, loop)
    _audit(db, loop, "reopened", before, payload.evidence, payload.idempotency_key)
    db.commit()
    db.refresh(loop)
    return _snapshot(loop)


@router.post("/{loop_id}/dependencies")
def add_dependency(loop_id: str, payload: DependencyInput, request: Request, db: Session = Depends(get_db)):
    person_id, caregiver_id = _scope(request, db)
    dependent = _get_scoped(db, person_id, caregiver_id, loop_id, for_update=True)
    prerequisite = _get_scoped(db, person_id, caregiver_id, payload.prerequisite_id, for_update=True)
    if dependent.id == prerequisite.id:
        raise HTTPException(status_code=422, detail="A loop cannot depend on itself")
    if dependent.status in TERMINAL_STATES:
        raise HTTPException(status_code=409, detail="Cannot add a dependency to a terminal loop")
    # Adding prerequisite -> dependent is invalid if dependent already reaches prerequisite.
    frontier, seen = [dependent.id], set()
    while frontier:
        node = frontier.pop()
        if node == prerequisite.id:
            raise HTTPException(status_code=409, detail="Dependency would create a cycle")
        if node in seen: continue
        seen.add(node)
        outgoing = db.query(OpenLoopDependency).filter(
            OpenLoopDependency.prerequisite_id == node, OpenLoopDependency.removed_at.is_(None)).all()
        frontier.extend(edge.dependent_id for edge in outgoing)
    existing = db.query(OpenLoopDependency).filter(
        OpenLoopDependency.prerequisite_id == prerequisite.id,
        OpenLoopDependency.dependent_id == dependent.id,
        OpenLoopDependency.removed_at.is_(None)).first()
    if existing:
        return {"id": existing.id, "already_exists": True}
    db.add(OpenLoopDependency(person_id=person_id, caregiver_id=caregiver_id,
                              prerequisite_id=prerequisite.id, dependent_id=dependent.id, mandatory=payload.mandatory))
    before = _snapshot(dependent)
    dependent.status = "blocked" if payload.mandatory and prerequisite.status != "closed" else dependent.status
    _reassess(db, dependent)
    _audit(db, dependent, "dependency_added", before, [{"prerequisite_id": prerequisite.id}])
    db.commit()
    return {"accepted": True, "prerequisite_id": prerequisite.id, "dependent_id": dependent.id, "blocked": dependent.status == "blocked"}


@router.get("/{loop_id}/occurrences")
def list_occurrences(loop_id: str, request: Request, limit: int = Query(100, ge=1, le=500), db: Session = Depends(get_db)):
    person_id, caregiver_id = _scope(request, db)
    loop = _get_scoped(db, person_id, caregiver_id, loop_id)
    rows = db.query(OpenLoopOccurrence).filter(
        OpenLoopOccurrence.loop_id == loop.id,
        OpenLoopOccurrence.person_id == person_id,
        OpenLoopOccurrence.caregiver_id == caregiver_id,
    ).order_by(OpenLoopOccurrence.occurred_at.asc()).limit(limit).all()
    return [{
        "id": row.id, "loop_id": row.loop_id, "source_type": row.source_type,
        "source_id": row.source_id, "source_fingerprint": row.source_fingerprint,
        "evidence": row.evidence, "occurred_at": row.occurred_at.isoformat(),
        "created_at": row.created_at.isoformat(),
    } for row in rows]


@router.get("/{loop_id}/history")
def loop_history(loop_id: str, request: Request, limit: int = Query(100, ge=1, le=500), db: Session = Depends(get_db)):
    person_id, caregiver_id = _scope(request, db)
    loop = _get_scoped(db, person_id, caregiver_id, loop_id)
    events = db.query(OpenLoopEvent).filter(
        OpenLoopEvent.loop_id == loop.id, OpenLoopEvent.person_id == person_id,
        OpenLoopEvent.caregiver_id == caregiver_id).order_by(OpenLoopEvent.created_at.asc()).limit(limit).all()
    return [{"id": e.id, "event_type": e.event_type, "before_state": e.before_state, "after_state": e.after_state,
             "evidence": e.evidence, "actor": e.actor, "created_at": e.created_at} for e in events]


@router.post("/reassess")
def reassess_open_loops(request: Request, db: Session = Depends(get_db)):
    person_id, caregiver_id = _scope(request, db)
    loops = db.query(OpenLoopRecord).filter(
        OpenLoopRecord.person_id == person_id, OpenLoopRecord.caregiver_id == caregiver_id,
        OpenLoopRecord.status.notin_(TERMINAL_STATES)).limit(500).all()
    changed = []
    for loop in loops:
        before = _snapshot(loop)
        # Recompute blocking from current mandatory prerequisites.
        deps = db.query(OpenLoopDependency).filter(
            OpenLoopDependency.dependent_id == loop.id, OpenLoopDependency.removed_at.is_(None),
            OpenLoopDependency.mandatory.is_(True)).all()
        unresolved = any(_get_scoped(db, person_id, caregiver_id, dep.prerequisite_id).status != "closed" for dep in deps)
        if unresolved:
            loop.status = "blocked"
        elif loop.status == "blocked":
            loop.status = "open"
        _reassess(db, loop)
        after = _snapshot(loop)
        state_changed = (
            before["status"] != after["status"]
            or before["priority"] != after["priority"]
            or before["priority_reasons"] != after["priority_reasons"]
        )
        # Every explicit reassessment is recorded, even when the decision is
        # unchanged. This makes "nothing changed" an auditable result rather
        # than an invisible operation.
        _audit(db, loop, "reassessed", before, [{
            "as_of": datetime.now(timezone.utc).isoformat(),
            "state_changed": state_changed,
        }])
        if state_changed:
            changed.append(loop.id)
    db.commit()
    return {
        "scanned": len(loops),
        "changed_ids": changed,
        "audited_count": len(loops),
        "as_of": datetime.now(timezone.utc),
    }


logger = logging.getLogger(__name__)
ABANDONMENT_INACTIVITY_DAYS = 30
OPEN_LOOP_MAINTENANCE_INTERVAL_SECONDS = 900


async def open_loop_maintenance_loop():
    """Periodic DB-backed review flagging; never closes an item automatically."""
    while True:
        await asyncio.sleep(OPEN_LOOP_MAINTENANCE_INTERVAL_SECONDS)
        db = SessionLocal()
        try:
            now = datetime.now(timezone.utc)
            cutoff = now - timedelta(days=ABANDONMENT_INACTIVITY_DAYS)
            rows = db.query(OpenLoopRecord).filter(
                OpenLoopRecord.status.in_({"open", "waiting"}),
                OpenLoopRecord.updated_at <= cutoff,
            ).limit(500).all()
            changed = 0
            for loop in rows:
                before = _snapshot(loop)
                prior_update = loop.updated_at
                loop.status = "review_abandoned"
                loop.inactive_since = prior_update.replace(tzinfo=timezone.utc) if prior_update and prior_update.tzinfo is None else prior_update
                loop.updated_at = now
                loop.version += 1
                loop.priority_reasons = list(dict.fromkeys([*(loop.priority_reasons or []), "No recorded loop activity for 30 days; human review required"]))
                _audit(db, loop, "abandoned_review_flagged", before, [{"policy_days": ABANDONMENT_INACTIVITY_DAYS}])
                changed += 1
            if changed:
                db.commit()
            else:
                db.rollback()
        except Exception:
            db.rollback()
            logger.exception("Open-loop maintenance failed; it will retry on the next interval")
        finally:
            db.close()
