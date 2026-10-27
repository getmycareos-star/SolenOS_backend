from datetime import datetime, timedelta, timezone
from typing import Literal
import hashlib
import json
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


class HandoffSummaryInput(BaseModel):
    sections: dict[str, str] = Field(default_factory=dict)
    source_record_ids: list[str] = Field(default_factory=list, max_length=100)
    caregiver_reviewed: bool
    prepared_at: datetime | None = None

    @field_validator("sections")
    @classmethod
    def valid_summary_sections(cls, value: dict[str, str]) -> dict[str, str]:
        allowed = {"lcr_summary", "open_loops", "appointments", "documents"}
        if not value or len(value) > 4 or not set(value).issubset(allowed):
            raise ValueError("summary sections must use one or more supported care scope names")
        cleaned = {}
        for key, text in value.items():
            if not isinstance(text, str) or not text.strip() or len(text) > 20000:
                raise ValueError("each summary section must contain nonblank text of at most 20000 characters")
            cleaned[key] = text.strip()
        if sum(len(text) for text in cleaned.values()) > 20000:
            raise ValueError("combined summary text exceeds 20000 characters")
        return cleaned

    @field_validator("source_record_ids")
    @classmethod
    def unique_source_ids(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value]
        if any(not item for item in cleaned):
            raise ValueError("source record IDs cannot be blank")
        if len(cleaned) != len(set(cleaned)):
            raise ValueError("source record IDs must be unique")
        return cleaned


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


@router.get("/directory/recipients")
def list_handoff_recipients(request: Request, db: Session = Depends(get_db)):
    person_id, caregiver_id = _scope(request, db)
    if not _has_required_consent(db, person_id, caregiver_id):
        db.add(AuditLogEvent(
            id=str(uuid.uuid4()), actor_id=caregiver_id, subject_id=person_id,
            person_id=person_id, caregiver_id=caregiver_id,
            action="handoff.recipients.list", target_type="handoff_directory",
            target_id=person_id, occurred_at=datetime.now(timezone.utc),
            outcome="denied", request_id=request.headers.get("x-request-id") or str(uuid.uuid4()),
            policy_version=POLICY_VERSION,
            record_metadata={"reason_code": "required_consent_missing"},
        ))
        db.commit()
        raise HTTPException(status_code=403, detail="Required consent is missing or revoked")
    caregivers = db.query(Caregiver).filter(
        Caregiver.person_id == person_id,
        Caregiver.id != caregiver_id,
    ).order_by(Caregiver.name.asc(), Caregiver.id.asc()).all()
    items = [
        {
            "caregiver_id": row.id,
            "name": row.name,
            "relationship": row.relationship,
            "can_receive_handoff": _has_required_consent(db, person_id, row.id),
        }
        for row in caregivers
    ]
    db.add(AuditLogEvent(
        id=str(uuid.uuid4()), actor_id=caregiver_id, subject_id=person_id,
        person_id=person_id, caregiver_id=caregiver_id,
        action="handoff.recipients.list", target_type="handoff_directory",
        target_id=person_id, occurred_at=datetime.now(timezone.utc),
        outcome="allowed", request_id=request.headers.get("x-request-id") or str(uuid.uuid4()),
        policy_version=POLICY_VERSION, record_metadata={"result_count": len(items)},
    ))
    db.commit()
    return {"items": items, "count": len(items)}


@router.get("/inbox")
def list_handoff_inbox(request: Request, db: Session = Depends(get_db)):
    person_id, caregiver_id = _scope(request, db)
    if not _has_required_consent(db, person_id, caregiver_id):
        db.add(AuditLogEvent(
            id=str(uuid.uuid4()), actor_id=caregiver_id, subject_id=person_id,
            person_id=person_id, caregiver_id=caregiver_id,
            action="handoff.inbox.list", target_type="handoff_inbox",
            target_id=caregiver_id, occurred_at=datetime.now(timezone.utc),
            outcome="denied", request_id=request.headers.get("x-request-id") or str(uuid.uuid4()),
            policy_version=POLICY_VERSION,
            record_metadata={"reason_code": "required_consent_missing"},
        ))
        db.commit()
        raise HTTPException(status_code=403, detail="Required consent is missing or revoked")

    now = datetime.now(timezone.utc)
    rows = db.query(HandoffRecord, HandoffRecipient, Caregiver).join(
        HandoffRecipient, HandoffRecipient.handoff_id == HandoffRecord.id
    ).join(
        Caregiver, Caregiver.id == HandoffRecord.created_by
    ).filter(
        HandoffRecord.person_id == person_id,
        HandoffRecipient.recipient_id == caregiver_id,
        HandoffRecipient.access_status.in_(["pending", "accepted"]),
        HandoffRecipient.revoked_at.is_(None),
        HandoffRecord.handoff_status != "revoked",
    ).order_by(HandoffRecord.created_at.desc(), HandoffRecord.id.desc()).all()

    items = []
    for handoff, recipient, creator in rows:
        if _aware(handoff.expires_at) and _aware(handoff.expires_at) <= now:
            continue
        if not _has_required_consent(db, person_id, handoff.created_by):
            continue
        items.append({
            "id": handoff.id,
            "from_caregiver_id": creator.id,
            "from_caregiver_name": creator.name,
            "from_caregiver_relationship": creator.relationship,
            "scope": handoff.scope_snapshot or [],
            "status": recipient.access_status,
            "created_at": handoff.created_at.isoformat() if handoff.created_at else None,
            "expires_at": handoff.expires_at.isoformat() if handoff.expires_at else None,
            "has_summary": handoff.summary_snapshot is not None,
        })
    db.add(AuditLogEvent(
        id=str(uuid.uuid4()), actor_id=caregiver_id, subject_id=person_id,
        person_id=person_id, caregiver_id=caregiver_id,
        action="handoff.inbox.list", target_type="handoff_inbox",
        target_id=caregiver_id, occurred_at=datetime.now(timezone.utc),
        outcome="allowed", request_id=request.headers.get("x-request-id") or str(uuid.uuid4()),
        policy_version=POLICY_VERSION, record_metadata={"result_count": len(items)},
    ))
    db.commit()
    return {"items": items, "count": len(items)}


@router.get("/outbox")
def list_handoff_outbox(request: Request, db: Session = Depends(get_db)):
    person_id, caregiver_id = _scope(request, db)
    # Owners must retain a narrow management path after consent is revoked so
    # they can locate and revoke existing shares. The outbox never returns summary
    # contents, and recipient access remains blocked by the read endpoints.
    owner_consent_active = _has_required_consent(db, person_id, caregiver_id)
    now = datetime.now(timezone.utc)
    rows = db.query(HandoffRecord).filter(
        HandoffRecord.person_id == person_id,
        HandoffRecord.created_by == caregiver_id,
    ).order_by(HandoffRecord.created_at.desc(), HandoffRecord.id.desc()).all()
    items = []
    for handoff in rows:
        item = _view(db, handoff)
        item["has_summary"] = handoff.summary_snapshot is not None
        item["is_expired"] = bool(
            _aware(handoff.expires_at) and _aware(handoff.expires_at) <= now
        )
        items.append(item)

    db.add(AuditLogEvent(
        id=str(uuid.uuid4()), actor_id=caregiver_id, subject_id=person_id,
        person_id=person_id, caregiver_id=caregiver_id,
        action="handoff.outbox.list", target_type="handoff_outbox",
        target_id=caregiver_id, occurred_at=now, outcome="allowed",
        request_id=request.headers.get("x-request-id") or str(uuid.uuid4()),
        policy_version=POLICY_VERSION, record_metadata={"result_count": len(items), "owner_consent_active": owner_consent_active},
    ))
    db.commit()
    return {"items": items, "count": len(items)}


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


@router.put("/{handoff_id}/summary")
def attach_handoff_summary(
    handoff_id: str, payload: HandoffSummaryInput,
    request: Request, db: Session = Depends(get_db),
):
    person_id, caregiver_id = _scope(request, db)
    handoff = db.query(HandoffRecord).filter(
        HandoffRecord.id == handoff_id, HandoffRecord.person_id == person_id
    ).first()
    if handoff is None or handoff.created_by != caregiver_id:
        if handoff is not None:
            _audit(db, request, person_id, caregiver_id, handoff.id, "handoff.summary.attach", "denied", reason_code="not_owner")
            db.commit()
        raise HTTPException(status_code=404, detail="Handoff not found")
    now = datetime.now(timezone.utc)
    if not _has_required_consent(db, person_id, caregiver_id):
        _audit(db, request, person_id, caregiver_id, handoff.id, "handoff.summary.attach", "denied", reason_code="required_consent_missing")
        db.commit()
        raise HTTPException(status_code=403, detail="Required consent is missing or revoked")
    if (
        handoff.handoff_status == "revoked"
        or (_aware(handoff.expires_at) and _aware(handoff.expires_at) <= now)
    ):
        _audit(db, request, person_id, caregiver_id, handoff.id, "handoff.summary.attach", "denied", reason_code="expired_or_revoked")
        db.commit()
        raise HTTPException(status_code=409, detail="This handoff has expired or been revoked")
    allowed_scopes = set(handoff.scope_snapshot or [])
    if not payload.sections or not set(payload.sections).issubset(allowed_scopes):
        _audit(db, request, person_id, caregiver_id, handoff.id, "handoff.summary.attach", "denied", reason_code="summary_outside_scope")
        db.commit()
        raise HTTPException(status_code=403, detail="Summary sections must be within the handoff's approved scope")
    if not payload.caregiver_reviewed:
        raise HTTPException(status_code=422, detail="The caregiver must review the summary before attaching it")
    if handoff.summary_snapshot is not None:
        raise HTTPException(status_code=409, detail="A summary snapshot is already attached; create a new handoff to replace it")

    snapshot = {
        "sections": payload.sections,
        "source_record_ids": payload.source_record_ids,
        "prepared_at": payload.prepared_at.isoformat() if payload.prepared_at else None,
        "stored_at": now.isoformat(),
        "caregiver_reviewed": True,
        "review_attestation": "client_declared_not_server_verified",
        "source_id_validation": "not_server_verified" if payload.source_record_ids else "not_supplied",
    }
    canonical = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    snapshot["content_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    handoff.summary_snapshot = snapshot
    handoff.summary_id = str(uuid.uuid4())
    _audit(db, request, person_id, caregiver_id, handoff.id, "handoff.summary.attach", "allowed")
    db.commit()
    return {
        "handoff_id": handoff.id,
        "summary_id": handoff.summary_id,
        "content_sha256": snapshot["content_sha256"],
        "review_attestation": snapshot["review_attestation"],
    }


@router.get("/{handoff_id}/summary")
def read_handoff_summary(handoff_id: str, request: Request, db: Session = Depends(get_db)):
    person_id, caregiver_id = _scope(request, db)
    handoff = db.query(HandoffRecord).filter(
        HandoffRecord.id == handoff_id, HandoffRecord.person_id == person_id
    ).first()
    if handoff is None:
        raise HTTPException(status_code=404, detail="Handoff not found")
    is_owner = handoff.created_by == caregiver_id
    if not is_owner:
        # Pending recipients may review the caregiver-reviewed summary before
        # accepting the handoff; they still cannot read handoff metadata until accepted.
        recipient = db.query(HandoffRecipient).filter(
            HandoffRecipient.handoff_id == handoff.id,
            HandoffRecipient.recipient_id == caregiver_id,
            HandoffRecipient.access_status.in_(["pending", "accepted"]),
            HandoffRecipient.revoked_at.is_(None),
        ).first()
        if (
            recipient is None
            or not _has_required_consent(db, person_id, caregiver_id)
            or not _has_required_consent(db, person_id, handoff.created_by)
            or handoff.handoff_status == "revoked"
            or (_aware(handoff.expires_at) and _aware(handoff.expires_at) <= datetime.now(timezone.utc))
        ):
            _audit(db, request, person_id, caregiver_id, handoff.id, "handoff.summary.read", "denied", reason_code="not_authorized_or_consent_revoked")
            db.commit()
            raise HTTPException(status_code=404, detail="Handoff summary not found")
    if not handoff.summary_snapshot:
        _audit(db, request, person_id, caregiver_id, handoff.id, "handoff.summary.read", "denied", reason_code="summary_missing")
        db.commit()
        raise HTTPException(status_code=404, detail="Handoff summary not found")

    snapshot = dict(handoff.summary_snapshot)
    expected_hash = snapshot.pop("content_sha256", None)
    canonical = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    actual_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    if not expected_hash or expected_hash != actual_hash:
        _audit(db, request, person_id, caregiver_id, handoff.id, "handoff.summary.read", "denied", reason_code="summary_integrity_failure")
        db.commit()
        raise HTTPException(status_code=409, detail="Handoff summary integrity check failed")
    snapshot["content_sha256"] = expected_hash
    _audit(db, request, person_id, caregiver_id, handoff.id, "handoff.summary.read", "allowed")
    db.commit()
    return {"handoff_id": handoff.id, "summary_id": handoff.summary_id, "summary": snapshot}


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
    if handoff.summary_snapshot is None:
        _audit(db, request, person_id, caregiver_id, handoff.id, "handoff.accept", "denied", reason_code="summary_missing")
        db.commit()
        raise HTTPException(status_code=409, detail="A caregiver-reviewed summary must be attached before accepting this handoff")
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
