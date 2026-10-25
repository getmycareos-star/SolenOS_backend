import hashlib
import hmac
import secrets
import re
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.anonymous_session import AnonymousSession
from app.models.account import CaregiverAccount
from app.models.care import Caregiver, Person

router = APIRouter(prefix="/sessions", tags=["caregiver sessions"])
SESSION_TTL_DAYS = 365
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


class AnonymousSessionCreate(BaseModel):
    name: str = Field(default="Care recipient", min_length=1, max_length=120)
    caregiver_name: str = Field(default="Caregiver", min_length=1, max_length=120)
    date_of_birth: Optional[date] = None


class AccountRegister(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=10, max_length=128)
    caregiver_name: str = Field(default="Caregiver", min_length=1, max_length=120)
    care_recipient_name: str = Field(default="Care recipient", min_length=1, max_length=120)
    anonymous_access_token: Optional[str] = Field(default=None, max_length=200)


class AccountLogin(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=128)
    mfa_code: Optional[str] = Field(default=None, min_length=6, max_length=32)


def _normalise_email(value: str) -> str:
    email = value.strip().lower()
    if not _EMAIL_RE.fullmatch(email):
        raise HTTPException(status_code=422, detail="Enter a valid email address")
    return email


def _hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=64)
    return "scrypt$%d$%d$%d$%s$%s" % (_SCRYPT_N, _SCRYPT_R, _SCRYPT_P, salt.hex(), derived.hex())


def _verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt_hex, expected_hex = encoded.split("$", 5)
        if algorithm != "scrypt":
            return False
        actual = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt_hex), n=int(n), r=int(r), p=int(p), dklen=len(bytes.fromhex(expected_hex)))
        return hmac.compare_digest(actual.hex(), expected_hex)
    except (ValueError, TypeError):
        return False


def _issue_account_session(db: Session, account: CaregiverAccount) -> dict:
    raw_token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(days=SESSION_TTL_DAYS)
    session = AnonymousSession(person_id=account.person_id, caregiver_id=account.caregiver_id, token_hash=hashlib.sha256(raw_token.encode("utf-8")).hexdigest(), expires_at=expires_at)
    db.add(session)
    db.commit()
    return {
        "account": {"id": account.id, "email": account.email},
        "person": {"id": account.person_id, "name": account.person.name, "date_of_birth": account.person.date_of_birth.isoformat() if account.person.date_of_birth else None},
        "caregiver": {"id": account.caregiver_id, "name": account.caregiver.name, "relationship": account.caregiver.relationship},
        "session_id": session.id, "access_token": raw_token, "token_type": "Bearer",
        "expires_at": expires_at.isoformat(), "expires_in_seconds": SESSION_TTL_DAYS * 24 * 60 * 60,
        "session_type": "account",
    }


@router.post("/register", status_code=201)
def register_account(payload: AccountRegister, request: Request, db: Session = Depends(get_db)):
    """Create an email/password caregiver account and a durable initial care record."""
    email = _normalise_email(payload.email)
    caregiver_name = payload.caregiver_name.strip()
    recipient_name = payload.care_recipient_name.strip()
    if not caregiver_name or not recipient_name:
        raise HTTPException(status_code=422, detail="Names must not be blank")
    if db.query(CaregiverAccount.id).filter(CaregiverAccount.email == email).first():
        raise HTTPException(status_code=409, detail="An account with this email already exists. Log in instead.")
    try:
        claimed_session = None
        person = None
        caregiver = None
        if payload.anonymous_access_token:
            token_hash = hashlib.sha256(payload.anonymous_access_token.strip().encode("utf-8")).hexdigest()
            claimed_session = db.query(AnonymousSession).filter(
                AnonymousSession.token_hash == token_hash,
                AnonymousSession.revoked_at.is_(None),
                AnonymousSession.expires_at > datetime.now(timezone.utc),
            ).with_for_update().first()
            if claimed_session is None:
                raise HTTPException(status_code=401, detail="The existing anonymous care session could not be verified. Log in to that session or create an account without claiming it.")
            if db.query(CaregiverAccount.id).filter(
                (CaregiverAccount.person_id == claimed_session.person_id) |
                (CaregiverAccount.caregiver_id == claimed_session.caregiver_id)
            ).first():
                raise HTTPException(status_code=409, detail="This care record is already linked to an account.")
            person = db.query(Person).filter(Person.id == claimed_session.person_id).with_for_update().first()
            caregiver = db.query(Caregiver).filter(Caregiver.id == claimed_session.caregiver_id).with_for_update().first()
            if person is None or caregiver is None or caregiver.person_id != person.id:
                raise HTTPException(status_code=409, detail="The existing care record is incomplete and cannot be safely claimed.")
            # Only replace generic labels; never overwrite names or care history the user already entered.
            if person.name.strip().lower() in {"care recipient", "person"}:
                person.name = recipient_name
            if caregiver.name.strip().lower() in {"caregiver", "family caregiver"}:
                caregiver.name = caregiver_name
            caregiver.email = email
            # Revoke every anonymous capability for this record before issuing its account-bound session.
            db.query(AnonymousSession).filter(
                AnonymousSession.person_id == person.id,
                AnonymousSession.caregiver_id == caregiver.id,
                AnonymousSession.revoked_at.is_(None),
            ).update({"revoked_at": datetime.now(timezone.utc)}, synchronize_session=False)
        else:
            person = Person(name=recipient_name)
            db.add(person)
            db.flush()
            caregiver = Caregiver(name=caregiver_name, email=email, relationship="Family", person_id=person.id)
            db.add(caregiver)
            db.flush()
        account = CaregiverAccount(email=email, password_hash=_hash_password(payload.password), person_id=person.id, caregiver_id=caregiver.id)
        db.add(account)
        db.flush()
        from app.api.account_security import _issue_token, _send_email, _now
        verification_token = _issue_token(db, account, "verify_email", request)
        db.commit()
        _send_email(email, "Verify your SolenOS account", f"Verify your email within 24 hours using this link:\\n{__import__('os').environ['APP_PUBLIC_URL'].rstrip('/')}/verify-email?token={verification_token}\\n\\nIf you did not request this, ignore this message.")
        return {"registered": True, "email_verification_required": True, "message": "Check your email to verify your account before signing in.", "account": {"id": account.id, "email": account.email}, "person": {"id": person.id, "name": person.name, "date_of_birth": person.date_of_birth.isoformat() if person.date_of_birth else None}, "caregiver": {"id": caregiver.id, "name": caregiver.name, "relationship": caregiver.relationship}, "claimed_existing_record": claimed_session is not None}
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="An account with this email already exists. Log in instead.")


@router.post("/login")
def login_account(payload: AccountLogin, request: Request, db: Session = Depends(get_db)):
    """Authenticate only verified accounts, enforcing durable throttling and MFA."""
    from app.api.account_security import check_login_throttle, record_login_attempt, verify_mfa_login
    email = _normalise_email(payload.email)
    ip = request.client.host if request.client else "unknown"
    check_login_throttle(db, email, ip)
    account = db.query(CaregiverAccount).filter(CaregiverAccount.email == email).first()
    if account is None or not _verify_password(payload.password, account.password_hash):
        record_login_attempt(db, email, ip, False)
        db.commit()
        raise HTTPException(status_code=401, detail="Email or password is incorrect")
    if account.email_verified_at is None:
        record_login_attempt(db, email, ip, False)
        db.commit()
        raise HTTPException(status_code=403, detail="Verify your email before signing in")
    if not verify_mfa_login(db, account, payload.mfa_code):
        record_login_attempt(db, email, ip, False)
        db.commit()
        raise HTTPException(status_code=401, detail="Email, password, or second-factor code is incorrect")
    record_login_attempt(db, email, ip, True)
    db.commit()
    return _issue_account_session(db, account)



@router.get("/me")
def current_session(request: Request, db: Session = Depends(get_db)):
    """Identify whether the current scoped bearer capability belongs to an account."""
    scheme, _, raw_token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not raw_token.strip():
        raise HTTPException(status_code=401, detail="A valid session is required")
    token_hash = hashlib.sha256(raw_token.strip().encode("utf-8")).hexdigest()
    session = db.query(AnonymousSession).filter(
        AnonymousSession.token_hash == token_hash,
        AnonymousSession.revoked_at.is_(None),
        AnonymousSession.expires_at > datetime.now(timezone.utc),
    ).first()
    if session is None:
        raise HTTPException(status_code=401, detail="A valid session is required")
    account = db.query(CaregiverAccount).filter(CaregiverAccount.caregiver_id == session.caregiver_id).first()
    if account is None:
        return {"authenticated": False, "account": None, "session_type": "anonymous"}
    return {"authenticated": True, "account": {"id": account.id, "email": account.email}, "session_type": "account"}


@router.post("/anonymous", status_code=201)
def create_anonymous_session(payload: AnonymousSessionCreate, db: Session = Depends(get_db)):
    """Create an optional anonymous care record without an account."""
    person_name = payload.name.strip()
    caregiver_name = payload.caregiver_name.strip() or "Caregiver"
    if not person_name:
        raise HTTPException(status_code=422, detail="name must not be blank")
    person = Person(name=person_name, date_of_birth=payload.date_of_birth)
    db.add(person)
    db.flush()
    caregiver = Caregiver(name=caregiver_name, email=None, relationship="Family", person_id=person.id)
    db.add(caregiver)
    db.flush()
    raw_token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(days=SESSION_TTL_DAYS)
    session = AnonymousSession(person_id=person.id, caregiver_id=caregiver.id, token_hash=hashlib.sha256(raw_token.encode("utf-8")).hexdigest(), expires_at=expires_at)
    db.add(session)
    db.commit()
    db.refresh(person)
    db.refresh(caregiver)
    return {
        "person": {"id": person.id, "name": person.name, "date_of_birth": person.date_of_birth.isoformat() if person.date_of_birth else None, "created_at": person.created_at.isoformat() if person.created_at else None},
        "caregiver": {"id": caregiver.id, "name": caregiver.name, "relationship": caregiver.relationship},
        "session_id": session.id, "access_token": raw_token, "token_type": "Bearer",
        "expires_at": expires_at.isoformat(), "expires_in_seconds": SESSION_TTL_DAYS * 24 * 60 * 60,
        "session_type": "anonymous",
    }


@router.post("/revoke")
def revoke_current_session(request: Request, db: Session = Depends(get_db)):
    """Revoke only the bearer session presented with this request."""
    scheme, _, raw_token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not raw_token.strip():
        raise HTTPException(status_code=401, detail="A valid session is required")
    token_hash = hashlib.sha256(raw_token.strip().encode("utf-8")).hexdigest()
    session = db.query(AnonymousSession).filter(
        AnonymousSession.token_hash == token_hash,
        AnonymousSession.revoked_at.is_(None),
        AnonymousSession.expires_at > datetime.now(timezone.utc),
    ).first()
    if session is None:
        raise HTTPException(status_code=401, detail="Session is invalid")
    session.revoked_at = datetime.now(timezone.utc)
    db.commit()
    return {"revoked": True}


@router.post("/refresh")
def refresh_anonymous_session(request: Request, db: Session = Depends(get_db)):
    """Rotate a still-valid bearer capability without changing record ownership."""
    session_id = getattr(request.state, "anonymous_session_id", None)
    if not session_id:
        raise HTTPException(status_code=401, detail="A valid session is required")
    session = db.query(AnonymousSession).filter(AnonymousSession.id == session_id, AnonymousSession.revoked_at.is_(None)).first()
    if not session:
        raise HTTPException(status_code=401, detail="Session is invalid")
    raw_token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(days=SESSION_TTL_DAYS)
    session.token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    session.expires_at = expires_at
    db.commit()
    return {
        "session_id": session.id, "person_id": session.person_id, "caregiver_id": session.caregiver_id,
        "access_token": raw_token, "token_type": "Bearer", "expires_at": expires_at.isoformat(),
        "expires_in_seconds": SESSION_TTL_DAYS * 24 * 60 * 60,
    }
