"""Session-scoped caregiver approval API for registered LCR follow-up actions.

Suggestions remain in the LCR assessment layer. This API creates an action only
when an authenticated caregiver submits an explicit approval.
"""
import hashlib
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app.core.database import get_db
from app.models.action import Action, ActionLifecycleAudit
from app.models.anonymous_session import AnonymousSession
from app.models.care import Caregiver, Evidence
from app.models.reminder import CareReminder
from app.schemas.action import ActionCandidate
from app.services.action_normalization import ActionNormalizationService
from app.services.change_intelligence_reconciliation import reconcile_person_change_intelligence

router = APIRouter(tags=["registered care actions"])

MEDICATION_CHANGE_DIRECTIVE = re.compile(
    r"^\s*(?:start|stop|increase|decrease|adjust|change|hold|skip|double|halve|switch|replace|discontinue|continue|take)\b.{0,100}\b(?:medication|medicine|drug|dose|dosage|pill|tablet|insulin|injection|prescription)\b",
    re.IGNORECASE,
)
DIRECT_CLINICAL_INSTRUCTION = re.compile(r"^\s*(?:diagnose|treat|prescribe|administer|perform|interpret|recommend)\b", re.IGNORECASE)
CONDITIONAL_FOLLOWUP = re.compile(r"\b(?:if|unless|until|only if|in case)\b", re.IGNORECASE)
MEDICATION_CHANGE_PREFIX = re.compile(
    r"^\s*(?:medication|medicine|drug|dose|dosage|pill|tablet|insulin|injection|prescription)\s*[:\-].*\b(?:start|stop|increase|decrease|adjust|change|hold|skip|double|halve|switch|replace|discontinue|continue|take)\b",
    re.IGNORECASE,
)
REGISTERED_STATUSES = {
    "approved", "in_progress", "completed", "cancelled", "deferred", "needs_clarification"
}
TRANSITIONS = {
    "approved": {"in_progress", "completed", "cancelled", "deferred", "needs_clarification"},
    "in_progress": {"completed", "cancelled", "deferred", "needs_clarification"},
    "deferred": {"approved", "in_progress", "cancelled", "needs_clarification"},
    "needs_clarification": {"approved", "deferred", "cancelled"},
    "completed": set(),
    "cancelled": set(),
}


def _session_scope(request: Request, db: Session) -> tuple[str, str]:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(status_code=401, detail="A valid SolenOS caregiver session is required")
    digest = hashlib.sha256(token.strip().encode("utf-8")).hexdigest()
    session = db.query(AnonymousSession).filter(
        AnonymousSession.token_hash == digest,
        AnonymousSession.revoked_at.is_(None),
        AnonymousSession.expires_at > datetime.now(timezone.utc),
    ).first()
    if session is None:
        raise HTTPException(status_code=401, detail="Caregiver session is invalid or expired")
    caregiver = db.query(Caregiver).filter(
        Caregiver.id == session.caregiver_id,
        Caregiver.person_id == session.person_id,
    ).first()
    if caregiver is None:
        raise HTTPException(status_code=403, detail="Caregiver is not linked to this care record")
    return str(session.person_id), str(session.caregiver_id)


class ApprovedActionCreate(BaseModel):
    # This literal is deliberately required: omission or false cannot register.
    explicit_approval: Literal[True]
    idempotency_key: str = Field(min_length=16, max_length=128)
    suggestion_id: str = Field(min_length=1, max_length=160)
    title: str = Field(min_length=3, max_length=500)
    action_type: Literal[
        "schedule", "call", "attend", "monitor", "ask", "obtain", "submit",
        "bring", "review", "confirm", "follow_up", "refer", "arrange",
        "record", "notify", "other"
    ] = "follow_up"
    rationale: str = Field(min_length=3, max_length=2000)
    limitations: Optional[str] = Field(default=None, max_length=2000)
    evidence_ids: list[str] = Field(min_length=1, max_length=20)
    responsible_caregiver_id: Optional[str] = None
    reminder_due_at: Optional[datetime] = None
    create_reminder: bool = False

    @field_validator("reminder_due_at")
    @classmethod
    def due_date_must_be_explicit_and_aware(cls, value):
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("Reminder due date must include a timezone offset")
        return value

    @field_validator("evidence_ids")
    @classmethod
    def unique_evidence_ids(cls, value):
        cleaned = list(dict.fromkeys(item.strip() for item in value if item and item.strip()))
        if not cleaned:
            raise ValueError("At least one supporting evidence reference is required")
        return cleaned


class ActionStatusChange(BaseModel):
    status: Literal["in_progress", "completed", "cancelled", "deferred", "needs_clarification", "approved"]
    expected_version: int = Field(ge=1)
    explicit_completion_confirmation: bool = False
    reason: Optional[str] = Field(default=None, max_length=2000)
    deferred_until: Optional[datetime] = None

    @field_validator("deferred_until")
    @classmethod
    def deferred_date_must_be_aware(cls, value):
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("Deferred-until date must include a timezone offset")
        return value


class RegisteredActionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    person_id: str
    source_evidence_id: str
    source_evidence_ids: Optional[list[str]] = None
    suggestion_id: str
    normalized_action: str
    original_text: str
    action_type: str
    status: str
    modality: str
    deadline: Optional[datetime] = None
    created_by_caregiver_id: str
    responsible_caregiver_id: Optional[str] = None
    approved_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    deferred_until: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    version: int
    linked_reminder_due_at: Optional[datetime] = None
    linked_reminder_status: Optional[str] = None


def _with_linked_reminder(db: Session, action: Action) -> Action:
    linked = db.query(CareReminder).filter(CareReminder.action_id == action.id).first()
    action.linked_reminder_due_at = linked.due_at if linked else None
    action.linked_reminder_status = linked.status if linked else None
    return action


def _audit(db: Session, action: Action, actor_id: str, old: Optional[str], new: str,
           reason: Optional[str], evidence_ids: Optional[list[str]] = None) -> None:
    previous_event = db.query(ActionLifecycleAudit).filter(
        ActionLifecycleAudit.action_id == action.id,
    ).order_by(ActionLifecycleAudit.created_at.desc()).first()
    event_time = datetime.now(timezone.utc)
    if previous_event is not None and previous_event.created_at is not None:
        previous_time = previous_event.created_at
        if previous_time.tzinfo is None or previous_time.utcoffset() is None:
            previous_time = previous_time.replace(tzinfo=timezone.utc)
        if event_time <= previous_time:
            event_time = previous_time + timedelta(microseconds=1)
    db.add(ActionLifecycleAudit(
        action_id=action.id,
        person_id=action.person_id,
        actor_caregiver_id=actor_id,
        previous_status=old,
        new_status=new,
        reason=reason,
        evidence_ids=evidence_ids or action.source_evidence_ids or [action.source_evidence_id],
        created_at=event_time,
    ))


@router.post("/registered-actions/{person_id}/approve", response_model=RegisteredActionRead, status_code=201)
def approve_follow_up(person_id: str, payload: ApprovedActionCreate, request: Request,
                      db: Session = Depends(get_db)):
    scoped_person_id, caregiver_id = _session_scope(request, db)
    if person_id != scoped_person_id:
        raise HTTPException(status_code=403, detail="This session is not authorised for that care record")
    if payload.responsible_caregiver_id and payload.responsible_caregiver_id != caregiver_id:
        raise HTTPException(status_code=403, detail="Assigning another caregiver requires a separate consent and handoff workflow")
    if payload.create_reminder and payload.reminder_due_at is None:
        raise HTTPException(status_code=422, detail="Select an explicit due date to create a reminder")
    if not payload.create_reminder and payload.reminder_due_at is not None:
        raise HTTPException(status_code=422, detail="A reminder due date requires explicit reminder creation")

    evidence_ids = payload.evidence_ids
    evidence_rows = db.query(Evidence).filter(
        Evidence.person_id == scoped_person_id,
        Evidence.id.in_(evidence_ids),
    ).all()
    if {row.id for row in evidence_rows} != set(evidence_ids):
        raise HTTPException(status_code=422, detail="Every supporting evidence item must exist in this care record")
    evidence_by_id = {row.id: row for row in evidence_rows}
    primary_evidence_id = evidence_ids[0]
    source_text = evidence_by_id[primary_evidence_id].source_text
    if not source_text or not source_text.strip():
        raise HTTPException(status_code=422, detail="The primary evidence item has no source text")

    # Caregiver approval cannot authorize a medication change. A follow-up to
    # contact a clinician about medication is allowed; direct medication
    # instructions are not registered as actions.
    if MEDICATION_CHANGE_DIRECTIVE.search(payload.title.strip()) or MEDICATION_CHANGE_PREFIX.search(payload.title.strip()):
        raise HTTPException(status_code=422, detail="Direct medication changes cannot be registered. Reframe this as contacting the prescribing clinician or pharmacist.")
    if DIRECT_CLINICAL_INSTRUCTION.search(payload.title.strip()):
        raise HTTPException(status_code=422, detail="Clinical diagnosis, treatment, prescribing or administration instructions cannot be registered as caregiver actions. Use a coordination or clinician-contact action instead.")

    if CONDITIONAL_FOLLOWUP.search(payload.title.strip()):
        raise HTTPException(status_code=422, detail="Conditional follow-ups cannot be registered as unconditional actions. Resolve the trigger or request clarification first.")

    safety_candidate = ActionCandidate(
        source_evidence_id=primary_evidence_id,
        source_passage=source_text,
        original_text=payload.title.strip(),
        extraction_confidence=1.0,  # validation-only; never persisted as extraction confidence
        actor_type="caregiver",
        actor_label="Caregiver",
        action_type=payload.action_type,
        normalized_action=payload.title.strip(),
        modality="recommended",
        is_explicit=True,
        is_medication_action=bool(re.search(r"\b(?:medication|medicine|drug|dose|dosage|pill|tablet|insulin|injection|prescription)\b", payload.title, re.IGNORECASE)),
    )
    safety = ActionNormalizationService().safety_check(safety_candidate)
    if not safety.is_safe_to_create:
        raise HTTPException(
            status_code=422,
            detail=safety.blocked_reason or "This follow-up is not safe to register as an action",
        )

    request_data = payload.model_dump(mode="json", exclude={"explicit_approval"})
    fingerprint = hashlib.sha256(
        json.dumps(request_data, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    identity_material = "|".join([scoped_person_id, ",".join(sorted(evidence_ids)), payload.title.strip().casefold()])
    identity_key = hashlib.sha256(identity_material.encode("utf-8")).hexdigest()
    existing = db.query(Action).filter(
        Action.person_id == scoped_person_id,
        Action.suggestion_id == payload.suggestion_id,
    ).first()
    if existing:
        if existing.registration_request_sha256 != fingerprint:
            raise HTTPException(status_code=409, detail="This LCR suggestion is already registered with a different approved payload")
        return _with_linked_reminder(db, existing)
    existing = db.query(Action).filter(
        Action.person_id == scoped_person_id,
        Action.action_identity_key == identity_key,
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail="An action with the same evidence and wording is already registered")
    existing = db.query(Action).filter(
        Action.person_id == scoped_person_id,
        Action.registration_key == payload.idempotency_key,
    ).first()
    if existing:
        if existing.registration_request_sha256 != fingerprint:
            raise HTTPException(status_code=409, detail="This approval key was already used for a different action")
        return _with_linked_reminder(db, existing)

    now = datetime.now(timezone.utc)
    action = Action(
        person_id=scoped_person_id,
        source_evidence_id=primary_evidence_id,
        source_evidence_ids=evidence_ids,
        source_passage=source_text,
        source_authority="caregiver",
        extraction_confidence=None,
        provenance_notes="Caregiver-approved LCR follow-up. Rationale: " + payload.rationale
            + (("\nLimitations: " + payload.limitations) if payload.limitations else ""),
        actor_type="caregiver",
        actor_id=caregiver_id,
        actor_label="Caregiver",
        subject_type="patient",
        action_type=payload.action_type,
        normalized_action=payload.title.strip(),
        original_text=payload.title.strip(),
        modality="recommended",
        # A caregiver-selected reminder time is not a source-established action deadline.
        deadline=None,
        status="approved",
        registration_key=payload.idempotency_key,
        suggestion_id=payload.suggestion_id,
        action_identity_key=identity_key,
        registration_request_sha256=fingerprint,
        approved_at=now,
        is_explicit=True,
        requires_confirmation=False,
        risk_tier="not_assessed",
        created_by_caregiver_id=caregiver_id,
        responsible_caregiver_id=caregiver_id,
        version=1,
    )
    db.add(action)
    db.flush()
    _audit(db, action, caregiver_id, None, "approved", "Explicit caregiver approval", evidence_ids)

    if payload.create_reminder:
        db.add(CareReminder(
            person_id=scoped_person_id,
            action_id=action.id,
            created_by_caregiver_id=caregiver_id,
            title=payload.title.strip()[:180],
            note="Reminder linked to caregiver-approved registered action.",
            due_at=payload.reminder_due_at.astimezone(timezone.utc),
            status="open",
        ))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.query(Action).filter(
            Action.person_id == scoped_person_id,
            Action.suggestion_id == payload.suggestion_id,
        ).first()
        if existing:
            if existing.registration_request_sha256 != fingerprint:
                raise HTTPException(status_code=409, detail="This LCR suggestion is already registered with a different approved payload")
            return _with_linked_reminder(db, existing)
        existing = db.query(Action).filter(
            Action.person_id == scoped_person_id,
            Action.action_identity_key == identity_key,
        ).first()
        if existing:
            raise HTTPException(status_code=409, detail="An action with the same evidence and wording is already registered")
        existing = db.query(Action).filter(
            Action.person_id == scoped_person_id,
            Action.registration_key == payload.idempotency_key,
        ).first()
        if existing and existing.registration_request_sha256 == fingerprint:
            return _with_linked_reminder(db, existing)
        raise HTTPException(status_code=409, detail="Action registration conflicted with an existing record")
    db.refresh(action)
    return _with_linked_reminder(db, action)


@router.get("/registered-actions/{person_id}", response_model=list[RegisteredActionRead])
def list_registered_actions(person_id: str, request: Request, db: Session = Depends(get_db)):
    scoped_person_id, _ = _session_scope(request, db)
    if person_id != scoped_person_id:
        raise HTTPException(status_code=403, detail="This session is not authorised for that care record")
    rows = db.query(Action, CareReminder.due_at, CareReminder.status).outerjoin(
        CareReminder, CareReminder.action_id == Action.id
    ).filter(
        Action.person_id == scoped_person_id,
        Action.status.in_(REGISTERED_STATUSES),
    ).order_by(Action.created_at.desc(), Action.id.asc()).all()
    actions = []
    for action, reminder_due_at, reminder_status in rows:
        action.linked_reminder_due_at = reminder_due_at
        action.linked_reminder_status = reminder_status
        actions.append(action)
    return actions


@router.get("/registered-actions/{action_id}/history")
def registered_action_history(action_id: str, request: Request, db: Session = Depends(get_db)):
    person_id, _ = _session_scope(request, db)
    action = db.query(Action).filter(Action.id == action_id, Action.person_id == person_id).first()
    if action is None:
        raise HTTPException(status_code=404, detail="Registered action not found")
    rows = db.query(ActionLifecycleAudit).filter(
        ActionLifecycleAudit.action_id == action.id,
        ActionLifecycleAudit.person_id == person_id,
    ).order_by(ActionLifecycleAudit.created_at.asc(), ActionLifecycleAudit.id.asc()).all()
    return [{
        "id": row.id,
        "action_id": row.action_id,
        "actor_caregiver_id": row.actor_caregiver_id,
        "previous_status": row.previous_status,
        "new_status": row.new_status,
        "reason": row.reason,
        "evidence_ids": row.evidence_ids or [],
        "created_at": row.created_at,
    } for row in rows]


@router.patch("/registered-actions/{action_id}/status", response_model=RegisteredActionRead)
def change_registered_action_status(action_id: str, payload: ActionStatusChange,
                                    request: Request, db: Session = Depends(get_db)):
    person_id, caregiver_id = _session_scope(request, db)
    action = db.query(Action).filter(Action.id == action_id, Action.person_id == person_id).with_for_update().first()
    if action is None or action.status not in REGISTERED_STATUSES:
        raise HTTPException(status_code=404, detail="Registered action not found")
    action_owner_id = action.responsible_caregiver_id or action.created_by_caregiver_id
    if action_owner_id != caregiver_id:
        raise HTTPException(status_code=403, detail="Only the responsible caregiver can change this registered action")
    if action.version != payload.expected_version:
        raise HTTPException(status_code=409, detail="Action changed since it was loaded; refresh before retrying")
    old_status = action.status
    if payload.status not in TRANSITIONS.get(old_status, set()):
        raise HTTPException(status_code=409, detail=f"Transition from {old_status} to {payload.status} is not allowed")
    if payload.status == "completed" and not payload.explicit_completion_confirmation:
        raise HTTPException(status_code=422, detail="Explicit caregiver completion confirmation is required")
    if payload.status in {"cancelled", "deferred", "needs_clarification"} and not (payload.reason and payload.reason.strip()):
        raise HTTPException(status_code=422, detail="A reason is required for this status change")
    if payload.deferred_until is not None and payload.status != "deferred":
        raise HTTPException(status_code=422, detail="deferred_until is only valid when deferring an action")
    linked_reminder = db.query(CareReminder).filter(
        CareReminder.action_id == action.id,
        CareReminder.person_id == person_id,
    ).with_for_update().first()
    if payload.status == "deferred" and linked_reminder and payload.deferred_until is None:
        raise HTTPException(status_code=422, detail="Choose a new due date when deferring an action that has a linked reminder")
    action.status = payload.status
    # A caregiver's explicit "needs clarification" transition is a deterministic
    # blocker. It authorizes one source-grounded question through the shared LCR
    # reconciler; priority/confidence alone never does. Leaving the state removes
    # the blocker so the same question can be superseded by that lifecycle.
    action.requires_confirmation = payload.status == "needs_clarification"
    action.deferred_until = payload.deferred_until.astimezone(timezone.utc) if payload.deferred_until else None
    if payload.status == "completed":
        action.completed_at = datetime.now(timezone.utc)
    if payload.status == "cancelled":
        action.cancelled_at = datetime.now(timezone.utc)
    if linked_reminder and linked_reminder.status == "open":
        if payload.status == "completed":
            linked_reminder.status = "completed"
            linked_reminder.completed_at = datetime.now(timezone.utc)
            linked_reminder.completed_by_caregiver_id = caregiver_id
            linked_reminder.completion_note = "Linked registered action was explicitly confirmed completed."
        elif payload.status == "cancelled":
            linked_reminder.status = "cancelled"
            linked_reminder.cancelled_at = datetime.now(timezone.utc)
            linked_reminder.cancelled_by_caregiver_id = caregiver_id
        elif payload.status == "deferred":
            linked_reminder.due_at = payload.deferred_until.astimezone(timezone.utc)
    _audit(db, action, caregiver_id, old_status, payload.status,
           payload.reason.strip() if payload.reason else (
               "Caregiver explicitly confirmed completion" if payload.status == "completed" else None
           ))
    try:
        # Keep action state, its source-grounded question lifecycle transition,
        # and audit history in one transaction. This also recovers/reassesses
        # after restart because the action status is persisted as the source.
        reconcile_person_change_intelligence(db, person_id)
        db.commit()
    except StaleDataError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Action was changed concurrently; refresh before retrying")
    db.refresh(action)
    linked = db.query(CareReminder).filter(CareReminder.action_id == action.id).first()
    action.linked_reminder_due_at = linked.due_at if linked else None
    action.linked_reminder_status = linked.status if linked else None
    return action
