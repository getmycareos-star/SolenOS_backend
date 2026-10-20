"""Authentication primitives: scrypt passwords and opaque, revocable sessions."""
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional
from sqlalchemy.orm import Session
from app.models.auth_identity import AuthAccount, AuthSession

PASSWORD_N = 2**14
PASSWORD_R = 8
PASSWORD_P = 1
PASSWORD_DKLEN = 32
SESSION_HOURS = 12

def normalize_email(email: str) -> str:
    return email.strip().lower()

def hash_password(password: str) -> str:
    if len(password) < 12:
        raise ValueError("Password must contain at least 12 characters.")
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=PASSWORD_N, r=PASSWORD_R, p=PASSWORD_P, dklen=PASSWORD_DKLEN)
    return "scrypt:{n}:{r}:{p}:{salt}:{digest}".format(n=PASSWORD_N, r=PASSWORD_R, p=PASSWORD_P, salt=salt.hex(), digest=derived.hex())

def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt_hex, digest_hex = encoded.split(":", 5)
        if algorithm != "scrypt":
            return False
        actual = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt_hex), n=int(n), r=int(r), p=int(p), dklen=len(bytes.fromhex(digest_hex)))
        return hmac.compare_digest(actual.hex(), digest_hex)
    except (ValueError, TypeError, MemoryError):
        return False

def create_account(db: Session, email: str, password: str, display_name: Optional[str] = None) -> AuthAccount:
    normalized = normalize_email(email)
    if not normalized or "@" not in normalized or len(normalized) > 254:
        raise ValueError("A valid email address is required.")
    if db.query(AuthAccount).filter(AuthAccount.email == normalized).first():
        raise ValueError("An account with this email already exists.")
    account = AuthAccount(email=normalized, password_hash=hash_password(password), display_name=display_name)
    db.add(account)
    db.commit()
    db.refresh(account)
    return account

def authenticate_account(db: Session, email: str, password: str) -> Optional[AuthAccount]:
    account = db.query(AuthAccount).filter(AuthAccount.email == normalize_email(email)).first()
    if not account or not account.is_active or not verify_password(password, account.password_hash):
        return None
    return account

def create_session(db: Session, account: AuthAccount) -> tuple[str, AuthSession]:
    raw_token = secrets.token_urlsafe(32)
    digest = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    now = datetime.now(timezone.utc)
    session = AuthSession(account_id=account.id, token_digest=digest, expires_at=now + timedelta(hours=SESSION_HOURS))
    db.add(session)
    db.commit()
    db.refresh(session)
    return raw_token, session

def resolve_session(db: Session, raw_token: str) -> Optional[AuthAccount]:
    if not raw_token or len(raw_token) > 512:
        return None
    digest = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    session = db.query(AuthSession).filter(AuthSession.token_digest == digest).first()
    now = datetime.now(timezone.utc)
    if not session or session.revoked_at is not None:
        return None
    expires = session.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires <= now:
        return None
    account = db.query(AuthAccount).filter(AuthAccount.id == session.account_id).first()
    if not account or not account.is_active:
        return None
    session.last_seen_at = now
    db.commit()
    return account

def revoke_session(db: Session, raw_token: str) -> bool:
    if not raw_token:
        return False
    digest = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    session = db.query(AuthSession).filter(AuthSession.token_digest == digest).first()
    if not session or session.revoked_at is not None:
        return False
    session.revoked_at = datetime.now(timezone.utc)
    db.commit()
    return True
