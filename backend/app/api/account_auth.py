import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.anonymous_session import AnonymousSession
from app.models.auth_account import AuthAccount, AuthSession
from app.models.care import Caregiver, Person
from app.schemas.account_auth import AccountLoginInput, AccountSignupInput

router = APIRouter(prefix="/auth", tags=["optional caregiver accounts"])
ACCOUNT_SESSION_TTL = timedelta(days=365)
CARE_SESSION_TTL = timedelta(days=365)


def _hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt:{salt.hex()}:{derived.hex()}"


def _verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, salt_hex, digest_hex = encoded.split(":", 2)
        if algorithm != "scrypt":
            return False
        actual = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt_hex), n=2**14, r=8, p=1, dklen=len(bytes.fromhex(digest_hex)))
        return hmac.compare_digest(actual.hex(), digest_hex)
    except (ValueError, TypeError, MemoryError):
        return False


def _issue_sessions(db: Session, account: AuthAccount, person: Person, caregiver: Caregiver):
    now = datetime.now(timezone.utc)
    account_token = secrets.token_urlsafe(32)
    care_token = secrets.token_urlsafe(32)
    account_expires = now + ACCOUNT_SESSION_TTL
    care_expires = account_expires
    account_session = AuthSession(
        account_id=account.id,
        token_digest=hashlib.sha256(account_token.encode("utf-8")).hexdigest(),
        expires_at=account_expires,
    )
    care_session = AnonymousSession(
        person_id=person.id,
        caregiver_id=caregiver.id,
        account_id=account.id,
        token_hash=hashlib.sha256(care_token.encode("utf-8")).hexdigest(),
        expires_at=care_expires,
    )
    db.add_all([account_session, care_session])
    db.commit()
    return {
        "account": {"id": account.id, "email": account.email, "display_name": account.display_name},
        "account_token": account_token,
        "account_expires_at": account_expires.isoformat(),
        "person": {"id": person.id, "name": person.name, "date_of_birth": person.date_of_birth.isoformat() if person.date_of_birth else None},
        "caregiver": {"id": caregiver.id, "name": caregiver.name, "relationship": caregiver.relationship},
        "session_id": care_session.id,
        "access_token": care_token,
        "token_type": "Bearer",
        "expires_at": care_expires.isoformat(),
        "expires_in_seconds": int(CARE_SESSION_TTL.total_seconds()),
        "auth_mode": "account",
    }


def _resolve_account(db: Session, authorization: str | None) -> tuple[AuthAccount, AuthSession]:
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="A valid account session is required")
    digest = hashlib.sha256(token.strip().encode("utf-8")).hexdigest()
    session = db.query(AuthSession).filter(
        AuthSession.token_digest == digest,
        AuthSession.revoked_at.is_(None),
        AuthSession.expires_at > datetime.now(timezone.utc),
    ).first()
    if session is None:
        raise HTTPException(status_code=401, detail="Account session is invalid or expired")
    account = db.query(AuthAccount).filter(AuthAccount.id == session.account_id, AuthAccount.is_active.is_(True)).first()
    if account is None:
        raise HTTPException(status_code=401, detail="Account is unavailable")
    session.last_seen_at = datetime.now(timezone.utc)
    db.commit()
    return account, session


@router.post("/signup", status_code=201)
def signup(payload: AccountSignupInput, db: Session = Depends(get_db)):
    if db.query(AuthAccount).filter(AuthAccount.email == payload.email).first():
        raise HTTPException(status_code=409, detail="An account with this email already exists")
    person = Person(name=payload.care_recipient_name)
    db.add(person)
    db.flush()
    caregiver = Caregiver(name=payload.display_name, email=None, relationship="Family", person_id=person.id)
    db.add(caregiver)
    db.flush()
    account = AuthAccount(
        email=payload.email,
        password_hash=_hash_password(payload.password),
        display_name=payload.display_name,
        person_id=person.id,
        caregiver_id=caregiver.id,
    )
    db.add(account)
    try:
        db.flush()
        result = _issue_sessions(db, account, person, caregiver)
        return result
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="An account with this email already exists")


@router.post("/login")
def login(payload: AccountLoginInput, db: Session = Depends(get_db)):
    account = db.query(AuthAccount).filter(AuthAccount.email == payload.email).first()
    if account is None or not account.is_active or not _verify_password(payload.password, account.password_hash):
        raise HTTPException(status_code=401, detail="Email or password is incorrect")
    person = db.query(Person).filter(Person.id == account.person_id).first()
    caregiver = db.query(Caregiver).filter(Caregiver.id == account.caregiver_id, Caregiver.person_id == account.person_id).first()
    if person is None or caregiver is None:
        raise HTTPException(status_code=409, detail="The account's care record is unavailable; access was not recreated")
    return _issue_sessions(db, account, person, caregiver)


@router.get("/me")
def me(authorization: str | None = Header(default=None), db: Session = Depends(get_db)):
    account, session = _resolve_account(db, authorization)
    return {
        "account": {"id": account.id, "email": account.email, "display_name": account.display_name},
        "person_id": account.person_id,
        "caregiver_id": account.caregiver_id,
        "expires_at": session.expires_at.isoformat(),
    }


@router.post("/logout")
def logout(authorization: str | None = Header(default=None), db: Session = Depends(get_db)):
    account, session = _resolve_account(db, authorization)
    now = datetime.now(timezone.utc)
    session.revoked_at = now
    db.query(AnonymousSession).filter(
        AnonymousSession.account_id == account.id,
        AnonymousSession.revoked_at.is_(None),
    ).update({"revoked_at": now}, synchronize_session=False)
    db.commit()
    return {"revoked": True}
