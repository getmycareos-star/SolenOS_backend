"""Persistent, evidence-backed open-loop tracking for caregiver continuity."""
import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, Index, Integer, JSON, String, Text,
    UniqueConstraint, and_, or_
)
from sqlalchemy.orm import Session

from app.core.database import Base, get_db
from app.models.anonymous_session import AnonymousSession

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


class OpenLoopRecord(Base):
    __tablename__ = "open_loop_records"
    __table_args__ = (
        Index("ix_open_loop_scope_status", "person_id", "caregiver_id", "status"),
        Index("ix_open_loop_scope_deadline", "person_id", "caregiver_id", "deadline_at"),
        Index("ix_open_loop_source_fingerprint", "person_id", "source_fingerprint"),
    )
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=False, index=True)
    loop_type = Column(String(40), nullable=False)
    title = Column(String(240), nullable=False)
    description = Column(Text, nullable=False)
    status = Column(String(40), nullable=False, default="open")
    outcome_status = Column(String(32), nullable=False, default="outcome_unknown")
    owner_role = Column(String(100), nullable=True)
    owner_label = Column(String(200), nullable=True)
    source_type = Column(String(80), nullable=True)
    source_id = Column(String(160), nullable=True)
    source_fingerprint = Column(String(64), nullable=False)
    evidence = Column(JSON, nullable=False, default=list)
    uncertainty = Column(String(40), nullable=False, default="unknown")
    missing_information = Column(JSON, nullable=False, default=list)
    options = Column(JSON, nullable=False, default=list)
    request_text = Column(Text, nullable=True)
    recipient_label = Column(String(200), nullable=True)
    requested_at = Column(DateTime(timezone=True), nullable=True)
    last_contact_at = Column(DateTime(timezone=True), nullable=True)
    response_evidence = Column(JSON, nullable=False, default=list)
    action_evidence = Column(JSON, nullable=False, default=list)
    outcome_evidence = Column(JSON, nullable=False, default=list)
    verification_rule = Column(Text, nullable=True)
    deadline_at = Column(DateTime(timezone=True), nullable=True)
    deadline_source = Column(String(40), nullable=True)
    deadline_timezone = Column(String(80), nullable=True)
    consequence_of_delay = Column(String(40), nullable=True)
    priority = Column(String(16), nullable=False, default="normal")
    priority_reasons = Column(JSON, nullable=False, default=list)
    deferred_reason = Column(Text, nullable=True)
    review_at = Column(DateTime(timezone=True), nullable=True)
    inactive_since = Column(DateTime(timezone=True), nullable=True)
    closure_evidence = Column(JSON, nullable=False, default=list)
    closure_result = Column(String(40), nullable=True)
    closure_actor = Column(String(160), nullable=True)
    closed_at = Column(DateTime(timezone=True), nullable=True)
    cancellation_reason = Column(Text, nullable=True)
    recurrence_of_id = Column(String, ForeignKey("open_loop_records.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    version = Column(Integer, nullable=False, default=1)


class OpenLoopEvent(Base):
    __tablename__ = "open_loop_events"
    __table_args__ = (
        Index("ix_open_loop_events_record_created", "loop_id", "created_at"),
        UniqueConstraint("person_id", "idempotency_key", name="uq_open_loop_event_idempotency"),
    )
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    loop_id = Column(String, ForeignKey("open_loop_records.id", ondelete="CASCADE"), nullable=False, index=True)
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=False, index=True)
    event_type = Column(String(60), nullable=False)
    before_state = Column(JSON, nullable=True)
    after_state = Column(JSON, nullable=False, default=dict)
    evidence = Column(JSON, nullable=False, default=list)
    idempotency_key = Column(String(160), nullable=True)
    actor = Column(String(160), nullable=False, default="session_caregiver")
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))


class OpenLoopOccurrence(Base):
    __tablename__ = "open_loop_occurrences"
    __table_args__ = (
        UniqueConstraint("person_id", "idempotency_key", name="uq_open_loop_occurrence_idempotency"),
        UniqueConstraint("person_id", "source_type", "source_id", "loop_id", name="uq_open_loop_occurrence_source"),
        Index("ix_open_loop_occurrence_record_created", "loop_id", "created_at"),
    )
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    loop_id = Column(String, ForeignKey("open_loop_records.id", ondelete="CASCADE"), nullable=False, index=True)
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=False, index=True)
    source_type = Column(String(80), nullable=True)
    source_id = Column(String(160), nullable=True)
    source_fingerprint = Column(String(64), nullable=False)
    evidence = Column(JSON, nullable=False, default=list)
    idempotency_key = Column(String(160), nullable=True)
    occurred_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))


class OpenLoopDependency(Base):
    __tablename__ = "open_loop_dependencies"
    __table_args__ = (
        UniqueConstraint("prerequisite_id", "dependent_id", name="uq_open_loop_dependency_pair"),
        Index("ix_open_loop_dependency_dependent", "dependent_id"),
    )
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=False, index=True)
    prerequisite_id = Column(String, ForeignKey("open_loop_records.id", ondelete="CASCADE"), nullable=False)
    dependent_id = Column(String, ForeignKey("open_loop_records.id", ondelete="CASCADE"), nullable=False)
    mandatory = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    removed_at = Column(DateTime(timezone=True), nullable=True)


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
            return existing
    db.add(OpenLoopEvent(
        loop_id=loop.id, person_id=loop.person_id, caregiver_id=loop.caregiver_id,
        event_type=event_type, before_state=before, after_state=_snapshot(loop),
        evidence=evidence or [], idempotency_key=key, actor=actor,
    ))
    return None


def _get_scoped(db, person_id, caregiver_id, loop_id):
    loop = db.query(OpenLoopRecord).filter(
        OpenLoopRecord.id == loop_id,
        OpenLoopRecord.person_id == person_id,
        OpenLoopRecord.caregiver_id == caregiver_id,
    ).first()
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
    loop.version += 1
    return reasons


@router.post("", status_code=201)
def create_loop(payload: LoopCreate, request: Request, db: Session = Depends(get_db)):
    person_id, caregiver_id = _scope(request, db)
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
    if payload.idempotency_key:
        replay = db.query(OpenLoopOccurrence).filter(
            OpenLoopOccurrence.person_id == person_id,
            OpenLoopOccurrence.idempotency_key == payload.idempotency_key,
        ).first()
        if replay:
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
    _audit(db, loop, "created", None, payload.evidence, payload.idempotency_key)
    db.commit()
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
    rows = query.order_by(OpenLoopRecord.deadline_at.asc().nullslast(), OpenLoopRecord.updated_at.desc()).offset(offset).limit(limit).all()
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
            if prerequisite and prerequisite.status not in TERMINAL_STATES:
                blockers.append({"id": prerequisite.id, "title": prerequisite.title, "status": prerequisite.status})
        loop._mandatory_blockers = blockers
        item = _snapshot(loop)
        if overdue is None or item["overdue"] == overdue:
            items.append(item)
    return {"items": items, "limit": limit, "offset": offset, "count": len(items)}


@router.get("/{loop_id}")
def get_loop(loop_id: str, request: Request, db: Session = Depends(get_db)):
    person_id, caregiver_id = _scope(request, db)
    loop = _get_scoped(db, person_id, caregiver_id, loop_id)
    deps = db.query(OpenLoopDependency).filter(
        OpenLoopDependency.dependent_id == loop.id, OpenLoopDependency.removed_at.is_(None)).all()
    blockers = []
    for dep in deps:
        prerequisite = _get_scoped(db, person_id, caregiver_id, dep.prerequisite_id)
        if dep.mandatory and prerequisite.status not in TERMINAL_STATES:
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
    loop = _get_scoped(db, person_id, caregiver_id, loop_id)
    before = _snapshot(loop)
    changes = payload.model_dump(exclude_unset=True)
    evidence = changes.pop("evidence", [])
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
    loop = _get_scoped(db, person_id, caregiver_id, loop_id)
    if loop.loop_type not in {"awaiting_response", "professional_input", "follow_up"}:
        raise HTTPException(status_code=409, detail="This loop type does not accept a response event")
    if loop.loop_type == "professional_input" and not payload.evidence:
        raise HTTPException(status_code=422, detail="Professional input requires source evidence; caregiver text alone cannot establish it")
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
    loop = _get_scoped(db, person_id, caregiver_id, loop_id)
    before = _snapshot(loop)
    loop.action_evidence = [*(loop.action_evidence or []), {"text": payload.text, "evidence": payload.evidence, "recorded_at": datetime.now(timezone.utc).isoformat()}]
    _reassess(db, loop)
    _audit(db, loop, "action_recorded", before, payload.evidence, payload.idempotency_key)
    db.commit()
    db.refresh(loop)
    return _snapshot(loop)


@router.post("/{loop_id}/reconcile")
def reconcile_contradiction(loop_id: str, payload: EvidenceAction, request: Request, db: Session = Depends(get_db)):
    person_id, caregiver_id = _scope(request, db)
    loop = _get_scoped(db, person_id, caregiver_id, loop_id)
    if loop.loop_type != "contradiction":
        raise HTTPException(status_code=409, detail="Only contradiction loops can be reconciled")
    if not payload.evidence:
        raise HTTPException(status_code=422, detail="Reconciliation requires supporting evidence")
    before = _snapshot(loop)
    reconciliation = {
        "kind": "explicit_reconciliation",
        "decision": payload.text,
        "evidence": payload.evidence,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }
    loop.outcome_evidence = [*(loop.outcome_evidence or []), reconciliation]
    loop.outcome_status = "outcome_verified"
    _audit(db, loop, "contradiction_reconciled", before, payload.evidence, payload.idempotency_key)
    db.commit()
    db.refresh(loop)
    return _snapshot(loop)

@router.post("/{loop_id}/outcomes")
def record_outcome(loop_id: str, payload: EvidenceAction, request: Request, db: Session = Depends(get_db)):
    person_id, caregiver_id = _scope(request, db)
    loop = _get_scoped(db, person_id, caregiver_id, loop_id)
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
    loop = _get_scoped(db, person_id, caregiver_id, loop_id)
    if not payload.evidence or not payload.verification_rule or not (loop.verification_rule or payload.verification_rule):
        raise HTTPException(status_code=409, detail="Outcome verification requires evidence and an explicit verification rule")
    before = _snapshot(loop)
    loop.verification_rule = loop.verification_rule or payload.verification_rule
    loop.outcome_evidence = [*(loop.outcome_evidence or []), {"text": payload.text, "evidence": payload.evidence, "verification_rule": loop.verification_rule, "recorded_at": datetime.now(timezone.utc).isoformat()}]
    loop.outcome_status = "outcome_verified"
    _audit(db, loop, "outcome_verified", before, payload.evidence, payload.idempotency_key)
    db.commit()
    db.refresh(loop)
    return _snapshot(loop)


@router.post("/{loop_id}/close")
def close_loop(loop_id: str, payload: EvidenceAction, request: Request, db: Session = Depends(get_db)):
    person_id, caregiver_id = _scope(request, db)
    loop = _get_scoped(db, person_id, caregiver_id, loop_id)
    if loop.status in TERMINAL_STATES:
        raise HTTPException(status_code=409, detail="Loop is already terminal")
    blockers = db.query(OpenLoopDependency).filter(
        OpenLoopDependency.dependent_id == loop.id, OpenLoopDependency.removed_at.is_(None),
        OpenLoopDependency.mandatory.is_(True)).all()
    for dep in blockers:
        prerequisite = _get_scoped(db, person_id, caregiver_id, dep.prerequisite_id)
        if prerequisite.status not in TERMINAL_STATES:
            raise HTTPException(status_code=409, detail="A mandatory prerequisite remains unresolved")
    if not payload.evidence:
        raise HTTPException(status_code=409, detail="Closure requires evidence")
    if loop.loop_type in {"awaiting_response", "professional_input"} and not loop.response_evidence:
        raise HTTPException(status_code=409, detail="A response must be recorded before closure")
    if loop.loop_type == "professional_input" and not any(item.get("evidence") for item in (loop.response_evidence or []) if isinstance(item, dict)):
        raise HTTPException(status_code=409, detail="Professional input has not been substantiated by source evidence")
    if loop.loop_type == "missing_information" and (loop.missing_information or loop.outcome_status != "outcome_verified"):
        raise HTTPException(status_code=409, detail="Required information must be obtained, validated, and recorded as a verified outcome")
    if loop.loop_type == "contradiction" and (loop.outcome_status != "outcome_verified" or not any("reconciliation" in str(e).lower() for e in payload.evidence)):
        raise HTTPException(status_code=409, detail="Contradiction closure requires verified, explicit reconciliation evidence")
    if loop.outcome_status != "outcome_verified" and loop.loop_type in {"pending_action", "pending_decision", "deferred_decision", "unconfirmed_outcome", "follow_up", "unresolved_concern", "missing_information", "contradiction"}:
        raise HTTPException(status_code=409, detail="This loop requires a verified outcome before closure")
    before = _snapshot(loop)
    loop.status = "closed"
    loop.closure_evidence = payload.evidence
    loop.closure_result = "verified"
    loop.closure_actor = "session_caregiver"
    loop.closed_at = datetime.now(timezone.utc)
    _audit(db, loop, "closure_verified", before, payload.evidence, payload.idempotency_key)
    db.commit()
    db.refresh(loop)
    return {"accepted": True, "verification": "verified", "loop": _snapshot(loop)}


@router.post("/{loop_id}/cancel")
def cancel_loop(loop_id: str, payload: EvidenceAction, request: Request, db: Session = Depends(get_db)):
    person_id, caregiver_id = _scope(request, db)
    loop = _get_scoped(db, person_id, caregiver_id, loop_id)
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
    loop = _get_scoped(db, person_id, caregiver_id, loop_id)
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
    dependent = _get_scoped(db, person_id, caregiver_id, loop_id)
    prerequisite = _get_scoped(db, person_id, caregiver_id, payload.prerequisite_id)
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
    dependent.status = "blocked" if payload.mandatory and prerequisite.status not in TERMINAL_STATES else dependent.status
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
        unresolved = any(_get_scoped(db, person_id, caregiver_id, dep.prerequisite_id).status not in TERMINAL_STATES for dep in deps)
        if unresolved:
            loop.status = "blocked"
        elif loop.status == "blocked":
            loop.status = "open"
        _reassess(db, loop)
        after = _snapshot(loop)
        if before["status"] != after["status"] or before["priority"] != after["priority"] or before["priority_reasons"] != after["priority_reasons"]:
            _audit(db, loop, "reassessed", before)
            changed.append(loop.id)
    db.commit()
    return {"scanned": len(loops), "changed_ids": changed, "as_of": datetime.now(timezone.utc)}
