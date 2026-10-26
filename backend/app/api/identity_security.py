import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.anonymous_session import AnonymousSession
from app.models.caregiver_account import CaregiverAccount
from app.models.identity_security import (
    AccountRecoveryEvent, AccountRecoveryRequest, AccountSecurityEvent,
    CaregiverDevice, DeviceEnrollmentEvent, DeviceRevocation, DeviceTrustRecord,
    IdentityAuditEvent, LoginAttemptLimit,
)

router = APIRouter(prefix="/identity", tags=["identity security"])


def _scope(request: Request):
    person_id = getattr(request.state, "scoped_person_id", None)
    caregiver_id = getattr(request.state, "scoped_caregiver_id", None)
    if not person_id or not caregiver_id:
        raise HTTPException(status_code=401, detail="A valid SolenOS session is required")
    return str(person_id), str(caregiver_id)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class DeviceRegister(BaseModel):
    installation_key: str = Field(min_length=24, max_length=200)
    label: str = Field(default="This device", min_length=1, max_length=120)
    platform: str | None = Field(default=None, max_length=40)


class RecoveryRequestInput(BaseModel):
    email: str = Field(min_length=3, max_length=320)


@router.get("/devices")
def list_devices(request: Request, db: Session = Depends(get_db)):
    _, caregiver_id = _scope(request)
    devices = db.query(CaregiverDevice).filter(
        CaregiverDevice.caregiver_id == caregiver_id,
        CaregiverDevice.revoked_at.is_(None),
    ).order_by(CaregiverDevice.created_at.desc()).all()
    return {"devices": [{
        "id": item.id, "label": item.label, "platform": item.platform,
        "last_seen_at": item.last_seen_at.isoformat() if item.last_seen_at else None,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "trust_status": "unverified",
        "recovery_can_use_device_alone": False,
    } for item in devices]}


@router.post("/devices", status_code=201)
def register_device(payload: DeviceRegister, request: Request, db: Session = Depends(get_db)):
    person_id, caregiver_id = _scope(request)
    session_id = getattr(request.state, "anonymous_session_id", None)
    session = db.query(AnonymousSession).filter(
        AnonymousSession.id == session_id,
        AnonymousSession.person_id == person_id,
        AnonymousSession.caregiver_id == caregiver_id,
        AnonymousSession.revoked_at.is_(None),
    ).first()
    if not session:
        raise HTTPException(status_code=401, detail="The current care session could not be verified")
    key_hash = _hash(payload.installation_key)
    device = db.query(CaregiverDevice).filter(
        CaregiverDevice.caregiver_id == caregiver_id,
        CaregiverDevice.installation_key_hash == key_hash,
    ).first()
    if device and device.revoked_at is not None:
        raise HTTPException(status_code=409, detail="This device registration was revoked. Register a new device installation.")
    now = datetime.now(timezone.utc)
    if device is None:
        account = db.query(CaregiverAccount).filter(
            CaregiverAccount.person_id == person_id,
            CaregiverAccount.caregiver_id == caregiver_id,
        ).first()
        device = CaregiverDevice(
            account_id=account.id if account else None,
            caregiver_id=caregiver_id,
            installation_key_hash=key_hash,
            label=payload.label.strip() or "This device",
            platform=payload.platform,
            last_seen_at=now,
        )
        db.add(device)
        db.flush()
        db.add(DeviceTrustRecord(device_id=device.id, trust_status="unverified"))
        db.add(DeviceEnrollmentEvent(device_id=device.id, caregiver_id=caregiver_id, event_type="registered_unverified"))
        db.add(IdentityAuditEvent(actor_caregiver_id=caregiver_id, person_id=person_id, action="device_registered_unverified"))
    else:
        device.label = payload.label.strip() or device.label
        device.platform = payload.platform
        device.last_seen_at = now
        db.add(DeviceEnrollmentEvent(device_id=device.id, caregiver_id=caregiver_id, event_type="seen_again"))
    db.commit()
    return {"device": {"id": device.id, "label": device.label, "platform": device.platform, "trust_status": "unverified", "recovery_can_use_device_alone": False}}


@router.delete("/devices/{device_id}")
def revoke_device(device_id: str, request: Request, db: Session = Depends(get_db)):
    person_id, caregiver_id = _scope(request)
    device = db.query(CaregiverDevice).filter(
        CaregiverDevice.id == device_id,
        CaregiverDevice.caregiver_id == caregiver_id,
        CaregiverDevice.revoked_at.is_(None),
    ).first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    now = datetime.now(timezone.utc)
    device.revoked_at = now
    db.add(DeviceRevocation(device_id=device.id, caregiver_id=caregiver_id, reason="caregiver_revoked", revoked_at=now))
    db.add(DeviceEnrollmentEvent(device_id=device.id, caregiver_id=caregiver_id, event_type="revoked"))
    db.add(IdentityAuditEvent(actor_caregiver_id=caregiver_id, person_id=person_id, action="device_revoked"))
    if device.account_id:
        db.add(AccountSecurityEvent(account_id=device.account_id, caregiver_id=caregiver_id, event_type="device_revoked"))
    db.commit()
    return {"revoked": True, "device_id": device.id}


@router.post("/recovery-requests", status_code=202)
def request_account_recovery(payload: RecoveryRequestInput, request: Request, db: Session = Depends(get_db)):
    email = payload.email.strip().casefold()
    if len(email) > 320 or email.count("@") != 1:
        raise HTTPException(status_code=422, detail="Enter a valid email address.")
    local, _, domain = email.partition("@")
    if not local or "." not in domain or domain.startswith(".") or domain.endswith("."):
        raise HTTPException(status_code=422, detail="Enter a valid email address.")
    account = db.query(CaregiverAccount).filter(CaregiverAccount.email_normalized == email).first()
    ip = request.client.host if request.client else "unknown"
    rate_key = _hash("recovery:" + ip)
    now = datetime.now(timezone.utc)
    limit = db.query(LoginAttemptLimit).filter(LoginAttemptLimit.key_hash == rate_key).first()
    if limit and limit.blocked_until and limit.blocked_until > now:
        raise HTTPException(status_code=429, detail="Too many recovery requests. Try again later.")
    if limit is None:
        limit = LoginAttemptLimit(key_hash=rate_key, attempts=0, window_started_at=now, updated_at=now)
        db.add(limit)
    window_started = limit.window_started_at if limit else None
    if window_started and window_started.tzinfo is None:
        window_started = window_started.replace(tzinfo=timezone.utc)
    if not window_started or (now - window_started).total_seconds() > 3600:
        limit.window_started_at = now
        limit.attempts = 0
        limit.blocked_until = None
    limit.attempts = int(limit.attempts or 0) + 1
    limit.updated_at = now
    if limit.attempts > 5:
        limit.blocked_until = now + timedelta(hours=1)
        db.commit()
        raise HTTPException(status_code=429, detail="Too many recovery requests. Try again later.")
    request_context_hash = _hash(ip + ":" + request.headers.get("user-agent", "")[:256])
    record = AccountRecoveryRequest(
        account_id=account.id if account else None,
        email_hash=_hash(email),
        status="pending_verification" if account else "no_matching_account",
        request_context_hash=request_context_hash,
    )
    db.add(record)
    db.flush()
    db.add(AccountRecoveryEvent(
        recovery_request_id=record.id,
        event_type="requested",
        details="A recovery request was submitted; no device-only recovery is permitted.",
    ))
    if account:
        db.add(AccountSecurityEvent(account_id=account.id, caregiver_id=account.caregiver_id, event_type="recovery_requested"))
    db.commit()
    # Deliberately identical response whether the email exists or not.
    return {"accepted": True, "message": "If an account matches those details, additional verification is required before access can be restored.", "request_id": record.id}


@router.get("/recovery-status")
def recovery_status(request: Request, db: Session = Depends(get_db)):
    _, caregiver_id = _scope(request)
    account = db.query(CaregiverAccount).filter(CaregiverAccount.caregiver_id == caregiver_id).first()
    if not account:
        return {"account_exists": False, "recovery_available": False, "message": "Create an optional account to enable account recovery."}
    latest = db.query(AccountRecoveryRequest).filter(
        AccountRecoveryRequest.account_id == account.id
    ).order_by(AccountRecoveryRequest.requested_at.desc()).first()
    return {"account_exists": True, "recovery_available": True, "latest_request_status": latest.status if latest else None, "message": "Recovery requires verified account recovery methods; a previously used device alone is not sufficient."}
