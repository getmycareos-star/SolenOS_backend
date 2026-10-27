from datetime import datetime, timedelta, timezone
from typing import Literal
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.anonymous_session import AnonymousSession
from app.models.care import Caregiver
from app.models.collaboration import ConsentRecord
from app.models.privacy_delivery import (
    AuditLogEvent,
    HandoffAccessLog,
    HandoffAcknowledgement,
    HandoffRecipient,
    HandoffRecord,
)

router = APIRouter(prefix="/handoffs", tags=["care handoffs"])
ALLOWED_SCOPE = {"lcr_summary", "open_loops", "appointments", "documents"}
POLICY_VERSION = "handoff-v1"


def _scope(request: Request, db: Session) -> tuple[str, str]:
    person_id = getattr(request.state, "scoped_person_id", None)
    caregiver_id = getattr(request.state, "scoped_caregiver_id", None)
    if person_id and caregiver_id:
        return str(person_id), str(caregiver_id)
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(status_code=401, detail="A valid SolenOS anonymous session is required")
    import hashlib
    token_hash = hashlib.sha256(token.strip().encode("utf-8")).hexdigest()
    session = (
        db.query(AnonymousSession)
        .filter(
            AnonymousSession.token_hash == token_hash,
            AnonymousSession.revoked_at.is_(None),
            AnonymousSession.expires_at > datetime.now(timezone.utc),
        )
        .first()
    )
    if session is None:
        raise HTTPException(status_code=401, detail="Anonymous session is invalid or expired")
    return str(session.person_id), str(session.caregiver_id)


def _has_required_consent(db: Session, person_id: str, caregiver_id: str) -> bool:
    consent = db.query(ConsentRecord).filter(
        ConsentRecord.person_id == person_id,
        ConsentRecord.caregiver_id == caregiver_id,
    ).first()
    return bool(
        consent
        and consent.revoked_at is None
        and consent.medical_disclaimer_acknowledged
        and consent.privacy_model_acknowledged
        and consent.multi_caregiver_acknowledged
        and consent.no_advertising_acknowledged
    )


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _audit(db: Session, request: Request, person_id: str, caregiver_id: str,
           handoff_id: str, action: str, outcome: str, recipient_id: str | None = None,
           reason_code: str | None = None) -> None:
    request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    db.add(HandoffAccessLog(
        id=str(uuid.uuid4()), actor_id=caregiver_id, subject_id=person_id,
        person_id=person_id, caregiver_id=caregiver_id, handoff_id=handoff_id,
        recipient_id=recipient_id, action=action, decision=outcome,
        reason_code=reason_code, occurred_at=now, request_id=request_id,
        policy_version=POLICY_VERSION,
    ))
    db.add(AuditLogEvent(
        id=str(uuid.uuid4()), actor_id=caregiver_id, subject_id=person_id,
        person_id=person_id, caregiver_id=caregiver_id, action=action,
        target_type="handoff", target_id=handoff_id, occurred_at=now,
        outcome=outcome, request_id=request_id, policy_version=POLICY_VERSION,
        record_metadata={"recipient_id": recipient_id, "reason_code": reason_code},
    ))


class HandoffCreate(BaseModel):
    person_id: str
    recipient_caregiver_ids: list[str] = Field(min_length=1, max_length=10)
    scope: list[Literal["lcr_summary", "open_loops", "appointments", "documents"]] = Field(min_length=1)
    expires_at: datetime | None = None

    @field_validator("recipient_caregiver_ids")
    @classmethod
    def unique_recipients(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("recipient caregiver IDs must be unique")
        return value

    @field_validator("scope")
    @classmethod
    def unique_scope(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("scope entries must be unique")
        return value


class HandoffAcknowledge(BaseModel):
    acknowledgement: str = Field(min_length=1, max_length=2000)
    note: str | None = Field(default=None, max_length=5000)

    @field_validator("acknowledgement")
    @classmethod
    def nonblank_acknowledgement(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("acknowledgement cannot be blank")
        return value.strip()


def _view(db: Session, handoff: HandoffRecord) -> dict:
    recipients = db.query(HandoffRecipient).filter(
        HandoffRecipient.handoff_id == handoff.id
    ).order_by(HandoffRecipient.invited_at.asc()).all()
    return {
        "id": handoff.id,
        "person_id": handoff.person_id,
        "created_by": handoff.created_by,
        "status": handoff.handoff_status,
        "scope": handoff.scope_snapshot or [],
        "created_at": handoff.created_at,
        "expires_at": handoff.expires_at,
        "revoked_at": handoff.revoked_at,
        "recipients": [
            {"caregiver_id": r.recipient_id, "role": r.recipient_role,
             "status": r.access_status, "invited_at": r.invited_at,
             "accepted_at": r.accepted_at, "revoked_at": r.revoked_at}
            for r in recipients
        ],
    }


@router.post("", status_code=201)
def create_handoff(payload: HandoffCreate, request: Request, db: Session = Depends(get_db)):
    person_id, caregiver_id = _scope(request, db)
    if payload.person_id != person_id:
        raise HTTPException(status_code=403, detail="This session is not authorised for that care record")
    if not _has_required_consent(db, person_id, caregiver_id):
        now = datetime.now(timezone.utc)
        db.add(AuditLogEvent(
            id=str(uuid.uuid4()), actor_id=caregiver_id, subject_id=person_id,
            person_id=person_id, caregiver_id=caregiver_id, action="handoff.create",
            target_type="handoff_request", target_id=person_id, occurred_at=now,
            outcome="denied",
            request_id=request.headers.get("x-request-id") or str(uuid.uuid4()),
            policy_version=POLICY_VERSION,
            record_metadata={"reason_code": "required_consent_missing"},
        ))
        db.commit()
        raise HTTPException(
            status_code=403,
            detail="Required privacy and multi-caregiver consent must be accepted before creating a handoff",
        )
    if any(scope not in ALLOWED_SCOPE for scope in payload.scope):
        raise HTTPException(status_code=422, detail="Unsupported handoff scope")
    if caregiver_id in payload.recipient_caregiver_ids:
        raise HTTPException(status_code=422, detail="The initiating caregiver cannot be a recipient")
    recipients = db.query(Caregiver).filter(
        Caregiver.person_id == person_id,
        Caregiver.id.in_(payload.recipient_caregiver_ids),
    ).all()
    if {r.id for r in recipients} != set(payload.recipient_caregiver_ids):
        raise HTTPException(status_code=404, detail="One or more recipients are not linked to this care record")

    now = datetime.now(timezone.utc)
    expires_at = _aware(payload.expires_at) if payload.expires_at else now + timedelta(days=7)
    if expires_at <= now or expires_at > now + timedelta(days=30):
        raise HTTPException(status_code=422, detail="Handoff expiry must be within the next 30 days")
    handoff = HandoffRecord(
        id=str(uuid.uuid4()), actor_id=caregiver_id, subject_id=person_id,
        person_id=person_id, caregiver_id=caregiver_id, created_by=caregiver_id,
        handoff_status="pending", scope_snapshot=list(payload.scope), created_at=now,
        expires_at=expires_at, policy_version=POLICY_VERSION,
        record_metadata={"scope_version": 1},
    )
    db.add(handoff)
    db.flush()
    for recipient in recipients:
        db.add(HandoffRecipient(
            id=str(uuid.uuid4()), actor_id=caregiver_id, subject_id=person_id,
            person_id=person_id, caregiver_id=caregiver_id, handoff_id=handoff.id,
            recipient_id=recipient.id, recipient_role=recipient.relationship,
            access_status="pending", invited_at=now, policy_version=POLICY_VERSION,
        ))
    _audit(db, request, person_id, caregiver_id, handoff.id, "handoff.created", "allowed")
    db.commit()
    db.refresh(handoff)
    return _view(db, handoff)


@router.get("/{handoff_id}")
def get_handoff(handoff_id: str, request: Request, db: Session = Depends(get_db)):
    person_id, caregiver_id = _scope(request, db)
    handoff = db.query(HandoffRecord).filter(
        HandoffRecord.id == handoff_id, HandoffRecord.person_id == person_id
    ).first()
    if handoff is None:
        raise HTTPException(status_code=404, detail="Handoff not found")
    is_owner = handoff.created_by == caregiver_id
    recipient = db.query(HandoffRecipient).filter(
        HandoffRecipient.handoff_id == handoff.id,
        HandoffRecipient.recipient_id == caregiver_id,
        HandoffRecipient.access_status == "accepted",
        HandoffRecipient.revoked_at.is_(None),
    ).first()
    if not is_owner and (
        recipient is None or not _has_required_consent(db, person_id, caregiver_id) or not _has_required_consent(db, person_id, handoff.created_by)
    ):
        _audit(db, request, person_id, caregiver_id, handoff.id, "handoff.read", "denied", reason_code="not_authorized_or_consent_revoked")
        db.commit()
        raise HTTPException(status_code=404, detail="Handoff not found")
    if not is_owner and (
        handoff.handoff_status == "revoked"
        or (_aware(handoff.expires_at) and _aware(handoff.expires_at) <= datetime.now(timezone.utc))
    ):
        _audit(db, request, person_id, caregiver_id, handoff.id, "handoff.read", "denied", reason_code="expired_or_revoked")
        db.commit()
        raise HTTPException(status_code=404, detail="Handoff not found")
    _audit(db, request, person_id, caregiver_id, handoff.id, "handoff.read", "allowed")
    db.commit()
    return _view(db, handoff)


@router.post("/{handoff_id}/accept")
def accept_handoff(handoff_id: str, request: Request, db: Session = Depends(get_db)):
    person_id, caregiver_id = _scope(request, db)
    handoff = db.query(HandoffRecord).filter(
        HandoffRecord.id == handoff_id, HandoffRecord.person_id == person_id
    ).first()
    if handoff is None:
        raise HTTPException(status_code=404, detail="Handoff not found")
    now = datetime.now(timezone.utc)
    if handoff.handoff_status == "revoked" or (_aware(handoff.expires_at) and _aware(handoff.expires_at) <= now):
        _audit(db, request, person_id, caregiver_id, handoff.id, "handoff.accept", "denied", reason_code="expired_or_revoked")
        db.commit()
        raise HTTPException(status_code=410, detail="Handoff has expired or been revoked")
    recipient = db.query(HandoffRecipient).filter(
        HandoffRecipient.handoff_id == handoff.id,
        HandoffRecipient.recipient_id == caregiver_id,
    ).first()
    if recipient is None or recipient.revoked_at is not None or not _has_required_consent(db, person_id, caregiver_id) or not _has_required_consent(db, person_id, handoff.created_by):
        _audit(db, request, person_id, caregiver_id, handoff.id, "handoff.accept", "denied", reason_code="not_a_recipient")
        db.commit()
        raise HTTPException(status_code=404, detail="Handoff not found")
    if recipient.access_status != "accepted":
        recipient.access_status = "accepted"
        recipient.accepted_at = now
    handoff.handoff_status = "accepted"
    _audit(db, request, person_id, caregiver_id, handoff.id, "handoff.accept", "allowed", recipient_id=caregiver_id)
    db.commit()
    db.refresh(handoff)
    return _view(db, handoff)


@router.post("/{handoff_id}/acknowledge")
def acknowledge_handoff(handoff_id: str, payload: HandoffAcknowledge, request: Request, db: Session = Depends(get_db)):
    person_id, caregiver_id = _scope(request, db)
    handoff = db.query(HandoffRecord).filter(
        HandoffRecord.id == handoff_id, HandoffRecord.person_id == person_id
    ).first()
    if handoff is None:
        raise HTTPException(status_code=404, detail="Handoff not found")
    now = datetime.now(timezone.utc)
    recipient = db.query(HandoffRecipient).filter(
        HandoffRecipient.handoff_id == handoff.id,
        HandoffRecipient.recipient_id == caregiver_id,
        HandoffRecipient.access_status == "accepted",
        HandoffRecipient.revoked_at.is_(None),
    ).first()
    if recipient is None or not _has_required_consent(db, person_id, caregiver_id) or not _has_required_consent(db, person_id, handoff.created_by) or handoff.handoff_status == "revoked" or (_aware(handoff.expires_at) and _aware(handoff.expires_at) <= now):
        _audit(db, request, person_id, caregiver_id, handoff.id, "handoff.acknowledge", "denied", reason_code="not_authorized_or_expired")
        db.commit()
        raise HTTPException(status_code=404, detail="Handoff not found")
    acknowledgement = HandoffAcknowledgement(
        id=str(uuid.uuid4()), actor_id=caregiver_id, subject_id=person_id,
        person_id=person_id, caregiver_id=caregiver_id, handoff_id=handoff.id,
        recipient_id=caregiver_id, acknowledged_at=now,
        acknowledgement=payload.acknowledgement,
        note=payload.note.strip() if payload.note and payload.note.strip() else None,
        policy_version=POLICY_VERSION,
    )
    db.add(acknowledgement)
    _audit(db, request, person_id, caregiver_id, handoff.id, "handoff.acknowledge", "allowed", recipient_id=caregiver_id)
    db.commit()
    return {"handoff_id": handoff.id, "acknowledged": True, "acknowledged_at": now}


@router.post("/{handoff_id}/revoke")
def revoke_handoff(handoff_id: str, request: Request, db: Session = Depends(get_db)):
    person_id, caregiver_id = _scope(request, db)
    handoff = db.query(HandoffRecord).filter(
        HandoffRecord.id == handoff_id, HandoffRecord.person_id == person_id
    ).first()
    if handoff is None:
        raise HTTPException(status_code=404, detail="Handoff not found")
    if handoff.created_by != caregiver_id:
        _audit(db, request, person_id, caregiver_id, handoff.id, "handoff.revoke", "denied", reason_code="not_owner")
        db.commit()
        raise HTTPException(status_code=404, detail="Handoff not found")
    now = datetime.now(timezone.utc)
    if handoff.handoff_status != "revoked":
        handoff.handoff_status = "revoked"
        handoff.revoked_at = now
        for recipient in db.query(HandoffRecipient).filter(HandoffRecipient.handoff_id == handoff.id).all():
            recipient.access_status = "revoked"
            recipient.revoked_at = now
    _audit(db, request, person_id, caregiver_id, handoff.id, "handoff.revoke", "allowed")
    db.commit()
    db.refresh(handoff)
    return _view(db, handoff)
