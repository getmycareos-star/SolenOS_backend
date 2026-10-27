import base64
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.anonymous_session import AnonymousSession
from app.services.caregiver_profile_identity import ensure_caregiver_profile_for_session
from app.models.caregiver_account import CaregiverAccount
from app.models.caregiver_intelligence import CaregiverProfile
from app.models.identity_security import LoginAttemptLimit, AccountSecurityEvent

router = APIRouter(prefix="/accounts", tags=["caregiver accounts"])
PASSWORD_ITERATIONS = 600_000
SESSION_TTL_DAYS = 365


class AccountCreate(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=12, max_length=256)


class AccountLogin(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=256)


class AccountDelete(BaseModel):
    password: str = Field(min_length=1, max_length=256)


def _email(value: str) -> str:
    return value.strip().casefold()


def _validate_email(value: str) -> str:
    normalized = _email(value)
    if len(normalized) > 320 or normalized.count('@') != 1:
        raise HTTPException(status_code=422, detail='Enter a valid email address.')
    local, _, domain = normalized.partition('@')
    if not local or not domain or '.' not in domain or domain.startswith('.') or domain.endswith('.'):
        raise HTTPException(status_code=422, detail='Enter a valid email address.')
    return normalized


def _hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS)
    salt_text = base64.urlsafe_b64encode(salt).decode("ascii")
    digest_text = base64.urlsafe_b64encode(digest).decode("ascii")
    return "pbkdf2_sha256$" + str(PASSWORD_ITERATIONS) + "$" + salt_text + "$" + digest_text


def _verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, rounds, salt_text, digest_text = stored.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        salt = base64.urlsafe_b64decode(salt_text.encode("ascii"))
        expected = base64.urlsafe_b64decode(digest_text.encode("ascii"))
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(rounds))
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def _scoped_ids(request: Request) -> tuple[str, str]:
    person_id = getattr(request.state, "scoped_person_id", None)
    caregiver_id = getattr(request.state, "scoped_caregiver_id", None)
    if not person_id or not caregiver_id:
        raise HTTPException(status_code=401, detail="A valid care-record session is required")
    return str(person_id), str(caregiver_id)


def _account_json(account: CaregiverAccount):
    return {"id": account.id, "email": account.email_normalized, "created_at": account.created_at.isoformat() if account.created_at else None}


@router.get("/me")
def get_my_account(request: Request, db: Session = Depends(get_db)):
    person_id, caregiver_id = _scoped_ids(request)
    account = db.query(CaregiverAccount).filter(
        CaregiverAccount.person_id == person_id,
        CaregiverAccount.caregiver_id == caregiver_id,
    ).first()
    return {"account": _account_json(account) if account else None}


@router.post("", status_code=201)
def create_account(payload: AccountCreate, request: Request, db: Session = Depends(get_db)):
    person_id, caregiver_id = _scoped_ids(request)
    email = _validate_email(payload.email)
    if db.query(CaregiverAccount).filter(CaregiverAccount.email_normalized == email).first():
        raise HTTPException(status_code=409, detail="An account already uses this email address.")
    existing = db.query(CaregiverAccount).filter(CaregiverAccount.person_id == person_id).first()
    if existing:
        raise HTTPException(status_code=409, detail="This care record already has an account.")
    account = CaregiverAccount(
        email_normalized=email,
        password_hash=_hash_password(payload.password),
        person_id=person_id,
        caregiver_id=caregiver_id,
    )
    db.add(account)
    try:
        db.flush()
        session_id = getattr(request.state, "anonymous_session_id", None)
        session = db.query(AnonymousSession).filter(AnonymousSession.id == session_id).first() if session_id else None
        if not session or session.person_id != person_id or session.caregiver_id != caregiver_id:
            db.rollback()
            raise HTTPException(status_code=401, detail="Your care-record session could not be confirmed.")
        try:
            caregiver_profile = ensure_caregiver_profile_for_session(
                db, person_id=person_id, session_caregiver_id=caregiver_id, email=email
            )
        except ValueError as exc:
            db.rollback()
            raise HTTPException(status_code=409, detail="This caregiver identity cannot be linked safely.") from exc
        session.account_id = account.id
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise HTTPException(status_code=409, detail="We could not create this account. The email or care record may already be registered.")
    db.refresh(account)
    return {"account": _account_json(account), "caregiver_profile_id": caregiver_profile.id, "message": "Account created and linked to this care record."}


@router.post("/login")
def login(payload: AccountLogin, request: Request, db: Session = Depends(get_db)):
    email = _email(payload.email)
    ip = request.client.host if request.client else "unknown"
    key_hash = hashlib.sha256(("login:" + ip + ":" + email).encode("utf-8")).hexdigest()
    now = datetime.now(timezone.utc)
    limit = db.query(LoginAttemptLimit).filter(LoginAttemptLimit.key_hash == key_hash).first()
    blocked_until = limit.blocked_until if limit else None
    if blocked_until and blocked_until.tzinfo is None:
        blocked_until = blocked_until.replace(tzinfo=timezone.utc)
    if blocked_until and blocked_until > now:
        raise HTTPException(status_code=429, detail="Too many sign-in attempts. Try again later.")
    if limit is None:
        limit = LoginAttemptLimit(key_hash=key_hash, attempts=0, window_started_at=now, updated_at=now)
        db.add(limit)
    window_started = limit.window_started_at if limit else None
    if window_started and window_started.tzinfo is None:
        window_started = window_started.replace(tzinfo=timezone.utc)
    if not window_started or (now - window_started).total_seconds() > 900:
        limit.window_started_at = now
        limit.attempts = 0
        limit.blocked_until = None
    account = db.query(CaregiverAccount).filter(CaregiverAccount.email_normalized == email).first()
    if not account or not _verify_password(payload.password, account.password_hash):
        limit.attempts = int(limit.attempts or 0) + 1
        limit.updated_at = now
        if limit.attempts >= 8:
            limit.blocked_until = now.replace(microsecond=0) + timedelta(minutes=15)
        db.add(AccountSecurityEvent(account_id=account.id if account else None, caregiver_id=account.caregiver_id if account else None, event_type="login_failed"))
        db.commit()
        raise HTTPException(status_code=401, detail="Email or password is incorrect.")
    try:
        caregiver_profile = ensure_caregiver_profile_for_session(
            db,
            person_id=account.person_id,
            session_caregiver_id=account.caregiver_id,
            email=account.email_normalized,
        )
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="This caregiver identity cannot be linked safely.") from exc
    limit.attempts = 0
    limit.blocked_until = None
    limit.updated_at = now
    db.add(AccountSecurityEvent(account_id=account.id, caregiver_id=account.caregiver_id, event_type="login_succeeded"))
    raw_token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(days=SESSION_TTL_DAYS)
    session = AnonymousSession(
        person_id=account.person_id,
        caregiver_id=account.caregiver_id,
        account_id=account.id,
        token_hash=hashlib.sha256(raw_token.encode("utf-8")).hexdigest(),
        expires_at=expires_at,
    )
    db.add(session)
    db.commit()
    return {
        "account": _account_json(account),
        "session_id": session.id,
        "person_id": account.person_id,
        "caregiver_id": account.caregiver_id,
        "caregiver_profile_id": caregiver_profile.id,
        "access_token": raw_token,
        "token_type": "Bearer",
        "expires_at": expires_at.isoformat(),
        "expires_in_seconds": SESSION_TTL_DAYS * 24 * 60 * 60,
    }


@router.delete("/me")
def delete_my_account(payload: AccountDelete, request: Request, db: Session = Depends(get_db)):
    person_id, caregiver_id = _scoped_ids(request)
    account = db.query(CaregiverAccount).filter(
        CaregiverAccount.person_id == person_id,
        CaregiverAccount.caregiver_id == caregiver_id,
    ).first()
    if not account:
        raise HTTPException(status_code=404, detail="There is no account linked to this care record.")
    if not _verify_password(payload.password, account.password_hash):
        raise HTTPException(status_code=401, detail="Password is incorrect. The account was not deleted.")
    current_session_id = getattr(request.state, "anonymous_session_id", None)
    linked_sessions = db.query(AnonymousSession).filter(
        AnonymousSession.account_id == account.id,
        AnonymousSession.revoked_at.is_(None),
    ).all()
    now = datetime.now(timezone.utc)
    for linked_session in linked_sessions:
        if linked_session.id == current_session_id:
            linked_session.account_id = None
        else:
            linked_session.revoked_at = now
    # Account deletion removes the account email from the linked caregiver
    # profile while retaining the anonymous care-record identity and history.
    profile = db.query(CaregiverProfile).filter(
        CaregiverProfile.person_id == person_id,
        CaregiverProfile.session_caregiver_id == caregiver_id,
    ).first()
    if profile and profile.email and profile.email.casefold() == account.email_normalized.casefold():
        profile.email = None
    db.delete(account)
    db.commit()
    return {
        "deleted": True,
        "care_record_retained": True,
        "other_sessions_revoked": True,
        "message": "Account deleted. Other account-linked sessions were revoked; this device remains in its anonymous care-record session.",
    }
