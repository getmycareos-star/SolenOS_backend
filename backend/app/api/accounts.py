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
from app.models.caregiver_account import CaregiverAccount

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
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=409, detail="We could not create this account. The email or care record may already be registered.")
    db.refresh(account)
    return {"account": _account_json(account), "message": "Account created and linked to this care record."}


@router.post("/login")
def login(payload: AccountLogin, db: Session = Depends(get_db)):
    email = _email(payload.email)
    account = db.query(CaregiverAccount).filter(CaregiverAccount.email_normalized == email).first()
    if not account or not _verify_password(payload.password, account.password_hash):
        raise HTTPException(status_code=401, detail="Email or password is incorrect.")
    raw_token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(days=SESSION_TTL_DAYS)
    session = AnonymousSession(
        person_id=account.person_id,
        caregiver_id=account.caregiver_id,
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
    db.delete(account)
    db.commit()
    return {"deleted": True, "care_record_retained": True, "message": "Account deleted. Your existing anonymous care-record session remains available on this device."}
