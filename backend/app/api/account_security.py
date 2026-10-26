"""Caregiver account verification, recovery, MFA, and durable login throttling.

Mail delivery requires SMTP_HOST, SMTP_PORT, SMTP_USERNAME, SMTP_PASSWORD,
SMTP_FROM_EMAIL and APP_PUBLIC_URL. MFA encryption requires ACCOUNT_SECURITY_KEY
(a stable random secret of at least 32 bytes). Missing security configuration fails
closed rather than issuing plaintext secrets or bypassing a required factor.
"""
import base64
import hashlib
import hmac
import logging
import os
import secrets
import smtplib
import ssl
import time
import uuid
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.account import CaregiverAccount
from app.models.anonymous_session import AnonymousSession

router = APIRouter(prefix="/account-security", tags=["caregiver account security"])
logger = logging.getLogger(__name__)
TOKEN_TTLS = {"verify_email": timedelta(hours=24), "password_reset": timedelta(minutes=30), "account_recovery": timedelta(minutes=30)}
LOGIN_WINDOW = timedelta(minutes=15)
LOGIN_MAX_EMAIL = 8
LOGIN_MAX_IP = 30


def _now():
    return datetime.now(timezone.utc)


def _email_hash(email: str) -> str:
    key = os.getenv("ACCOUNT_SECURITY_KEY", "")
    if len(key.encode()) < 32:
        raise HTTPException(status_code=503, detail="Account security is not configured")
    return hmac.new(key.encode(), email.strip().lower().encode(), hashlib.sha256).hexdigest()


def _ip_hash(ip: str) -> str:
    # Hashing avoids persisting raw client addresses; the keyed hash prevents easy enumeration.
    key = os.getenv("ACCOUNT_SECURITY_KEY", "")
    if len(key.encode()) < 32:
        raise HTTPException(status_code=503, detail="Account security is not configured")
    return hmac.new(key.encode(), (ip or "unknown").encode(), hashlib.sha256).hexdigest()


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _security_key() -> bytes:
    raw = os.getenv("ACCOUNT_SECURITY_KEY", "")
    if len(raw.encode()) < 32:
        raise HTTPException(status_code=503, detail="Account security is not configured")
    return hashlib.sha256(raw.encode()).digest()


def _seal(value: bytes) -> str:
    from cryptography.fernet import Fernet
    key = base64.urlsafe_b64encode(_security_key())
    return Fernet(key).encrypt(value).decode()


def _unseal(value: str) -> bytes:
    from cryptography.fernet import Fernet, InvalidToken
    key = base64.urlsafe_b64encode(_security_key())
    try:
        return Fernet(key).decrypt(value.encode())
    except InvalidToken as exc:
        raise ValueError("invalid encrypted secret") from exc


def _totp(secret: bytes, at: int | None = None) -> str:
    import struct
    counter = int((at if at is not None else time.time()) // 30)
    digest = hmac.new(secret, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    number = (struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF) % 1_000_000
    return f"{number:06d}"


def _valid_totp(secret: bytes, code: str) -> bool:
    if not code.isdigit() or len(code) != 6:
        return False
    now = time.time()
    return any(hmac.compare_digest(_totp(secret, now + shift * 30), code) for shift in (-1, 0, 1))


def ensure_mail_configured():
    required = ["SMTP_HOST", "SMTP_USERNAME", "SMTP_PASSWORD", "SMTP_FROM_EMAIL", "APP_PUBLIC_URL"]
    if any(not os.getenv(key) for key in required):
        raise HTTPException(status_code=503, detail="Email delivery is not configured; account security action was not completed")


def _send_email(to_email: str, subject: str, body: str):
    ensure_mail_configured()
    try:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = os.environ["SMTP_FROM_EMAIL"]
        msg["To"] = to_email
        msg.set_content(body)
        with smtplib.SMTP(os.environ["SMTP_HOST"], int(os.getenv("SMTP_PORT", "587")), timeout=10) as server:
            server.starttls(context=ssl.create_default_context())
            server.login(os.environ["SMTP_USERNAME"], os.environ["SMTP_PASSWORD"])
            server.send_message(msg)
    except Exception as exc:
        # Do not return a token to the client as a fallback.
        raise HTTPException(status_code=503, detail="Email delivery failed; retry later") from exc


def _issue_token(db: Session, account: CaregiverAccount, purpose: str, request: Request | None = None) -> str:
    raw = secrets.token_urlsafe(32)
    expires = _now() + TOKEN_TTLS[purpose]
    ip = request.client.host if request and request.client else "unknown"
    db.execute(text("UPDATE account_security_tokens SET consumed_at=:now WHERE account_id=:account AND purpose=:purpose AND consumed_at IS NULL"), {"now": _now(), "account": account.id, "purpose": purpose})
    db.execute(text("DELETE FROM account_security_tokens WHERE expires_at < :cutoff"), {"cutoff": _now() - timedelta(days=7)})
    db.execute(text("""INSERT INTO account_security_tokens
        (id, account_id, purpose, token_hash, expires_at, created_at, request_ip_hash)
        VALUES (:id, :account, :purpose, :hash, :expires, :created, :ip)"""),
        {"id": str(uuid.uuid4()), "account": account.id, "purpose": purpose, "hash": _token_hash(raw), "expires": expires, "created": _now(), "ip": _ip_hash(ip)})
    db.flush()
    return raw


def _consume_token(db: Session, raw: str, purpose: str):
    lock_clause = " FOR UPDATE" if db.bind is not None and db.bind.dialect.name == "postgresql" else ""
    row = db.execute(text("""SELECT id, account_id, expires_at, consumed_at FROM account_security_tokens
        WHERE token_hash=:hash AND purpose=:purpose""" + lock_clause),
        {"hash": _token_hash(raw), "purpose": purpose}).mappings().first()
    expires_at = row["expires_at"] if row else None
    if isinstance(expires_at, str):
        expires_at = datetime.fromisoformat(expires_at)
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if not row or row["consumed_at"] is not None or expires_at <= _now():
        raise HTTPException(status_code=400, detail="Token is invalid, expired, or already used")
    consumed = db.execute(text("UPDATE account_security_tokens SET consumed_at=:now WHERE id=:id AND consumed_at IS NULL"),
                          {"now": _now(), "id": row["id"]})
    if consumed.rowcount != 1:
        raise HTTPException(status_code=400, detail="Token is invalid, expired, or already used")
    return db.query(CaregiverAccount).filter(CaregiverAccount.id == row["account_id"]).with_for_update().first()


def _revoke_sessions(db: Session, account: CaregiverAccount):
    db.query(AnonymousSession).filter(
        AnonymousSession.caregiver_id == account.caregiver_id,
        AnonymousSession.revoked_at.is_(None),
    ).update({"revoked_at": _now()}, synchronize_session=False)



def _lock_rate_limit_keys(db: Session, keys: list[str]):
    # Serialize concurrent checks for the same hashed account/IP keys on PostgreSQL.
    # Locks are transaction-scoped and require no external cache service.
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        for key in sorted(set(keys)):
            db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"), {"lock_key": key})


def check_login_throttle(db: Session, email: str, ip: str):
    ehash, ihash = _email_hash(email), _ip_hash(ip)
    _lock_rate_limit_keys(db, [f"login:email:{ehash}", f"login:ip:{ihash}"])
    since = _now() - LOGIN_WINDOW
    count = db.execute(text("""SELECT COUNT(*) FROM account_login_attempts
        WHERE attempted_at >= :since AND succeeded = false AND (email_hash=:email OR ip_hash=:ip)"""),
        {"since": since, "email": ehash, "ip": ihash}).scalar_one()
    email_count = db.execute(text("""SELECT COUNT(*) FROM account_login_attempts
        WHERE attempted_at >= :since AND succeeded = false AND email_hash=:email"""),
        {"since": since, "email": ehash}).scalar_one()
    if count >= LOGIN_MAX_IP or email_count >= LOGIN_MAX_EMAIL:
        raise HTTPException(status_code=429, detail="Too many sign-in attempts. Try again later.")



def check_security_action_throttle(db: Session, email: str, ip: str, purpose: str):
    """Persist abuse counters even for unknown email addresses to avoid enumeration."""
    ehash, ihash = _email_hash(email), _ip_hash(ip)
    _lock_rate_limit_keys(db, [f"security:{purpose}:email:{ehash}", f"security:ip:{ihash}"])
    since = _now() - timedelta(minutes=15)
    email_count = db.execute(text("""SELECT COUNT(*) FROM account_security_requests
        WHERE email_hash=:email AND purpose=:purpose AND requested_at >= :since"""),
        {"email": ehash, "purpose": purpose, "since": since}).scalar_one()
    ip_count = db.execute(text("""SELECT COUNT(*) FROM account_security_requests
        WHERE ip_hash=:ip AND requested_at >= :since"""),
        {"ip": ihash, "since": since}).scalar_one()
    if email_count >= 3 or ip_count >= 15:
        raise HTTPException(status_code=429, detail="Too many security requests. Try again later.")
    db.execute(text("DELETE FROM account_security_requests WHERE requested_at < :cutoff"), {"cutoff": _now() - timedelta(days=7)})
    db.execute(text("""INSERT INTO account_security_requests
        (id, email_hash, ip_hash, purpose, requested_at)
        VALUES (:id, :email, :ip, :purpose, :at)"""),
        {"id": str(uuid.uuid4()), "email": ehash, "ip": ihash, "purpose": purpose, "at": _now()})
    db.commit()


def record_login_attempt(db: Session, email: str, ip: str, succeeded: bool):
    db.execute(text("""INSERT INTO account_login_attempts
        (id, email_hash, ip_hash, attempted_at, succeeded)
        VALUES (:id, :email, :ip, :at, :success)"""),
        {"id": str(uuid.uuid4()), "email": _email_hash(email), "ip": _ip_hash(ip), "at": _now(), "success": succeeded})
    # Keep the persistent table bounded.
    db.execute(text("DELETE FROM account_login_attempts WHERE attempted_at < :cutoff"), {"cutoff": _now() - timedelta(days=7)})


class TokenRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)


class TokenConfirm(BaseModel):
    token: str = Field(min_length=20, max_length=200)


class PasswordResetConfirm(TokenConfirm):
    new_password: str = Field(min_length=10, max_length=128)


class RecoveryKeyConfirm(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    recovery_code: str = Field(min_length=12, max_length=100)
    new_password: str = Field(min_length=10, max_length=128)


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=10, max_length=128)


class DevicePasswordReset(BaseModel):
    new_password: str = Field(min_length=10, max_length=128)


class MfaConfirm(BaseModel):
    code: str = Field(min_length=6, max_length=12)


@router.post("/verification/request", status_code=202)
def request_email_verification(payload: TokenRequest, request: Request, db: Session = Depends(get_db)):
    ensure_mail_configured()
    email = payload.email.strip().lower()
    ip = request.client.host if request.client else "unknown"
    check_security_action_throttle(db, email, ip, "verify_email")
    account = db.query(CaregiverAccount).filter(CaregiverAccount.email == email).first()
    # Always generic to avoid account enumeration. Unknown accounts do not get a token.
    if account is not None and getattr(account, "email_verified_at", None) is None:
        raw = _issue_token(db, account, "verify_email", request)
        db.commit()
        try:
            _send_email(email, "Verify your SolenOS account", f"Verify your email within 24 hours using this link:\n{os.environ['APP_PUBLIC_URL'].rstrip('/')}/verify-email?token={raw}\n\nIf you did not request this, ignore this message.")
        except HTTPException as exc:
            logger.warning("Verification email delivery failed: %s", exc.detail)
    return {"message": "If the account exists and needs verification, instructions will be sent."}


@router.post("/verification/confirm")
def confirm_email_verification(payload: TokenConfirm, db: Session = Depends(get_db)):
    account = _consume_token(db, payload.token, "verify_email")
    if account is None:
        db.rollback()
        raise HTTPException(status_code=400, detail="Token is invalid, expired, or already used")
    account.email_verified_at = _now()
    db.commit()
    return {"verified": True}


@router.post("/password-reset/request", status_code=202)
def request_password_reset(payload: TokenRequest, request: Request, db: Session = Depends(get_db)):
    ensure_mail_configured()
    email = payload.email.strip().lower()
    ip = request.client.host if request.client else "unknown"
    check_security_action_throttle(db, email, ip, "password_reset")
    account = db.query(CaregiverAccount).filter(CaregiverAccount.email == email).first()
    if account is not None:
        raw = _issue_token(db, account, "password_reset", request)
        db.commit()
        try:
            _send_email(email, "Reset your SolenOS password", f"Reset your password within 30 minutes using this link:\n{os.environ['APP_PUBLIC_URL'].rstrip('/')}/reset-password?token={raw}\n\nIf you did not request this, ignore this message.")
        except HTTPException as exc:
            logger.warning("Password-reset email delivery failed: %s", exc.detail)
    return {"message": "If the account exists, password reset instructions will be sent."}


@router.post("/password-reset/confirm")
def confirm_password_reset(payload: PasswordResetConfirm, db: Session = Depends(get_db)):
    from app.api.anonymous_sessions import _hash_password
    account = _consume_token(db, payload.token, "password_reset")
    if account is None:
        db.rollback()
        raise HTTPException(status_code=400, detail="Token is invalid, expired, or already used")
    account.password_hash = _hash_password(payload.new_password)
    account.updated_at = _now()
    _revoke_sessions(db, account)
    db.commit()
    return {"password_reset": True, "sessions_revoked": True}


@router.post("/recovery/request", status_code=202)
def request_account_recovery(payload: TokenRequest, request: Request, db: Session = Depends(get_db)):
    ensure_mail_configured()
    email = payload.email.strip().lower()
    ip = request.client.host if request.client else "unknown"
    check_security_action_throttle(db, email, ip, "account_recovery")
    account = db.query(CaregiverAccount).filter(CaregiverAccount.email == email).first()
    if account is not None:
        raw = _issue_token(db, account, "account_recovery", request)
        db.commit()
        try:
            _send_email(email, "Recover your SolenOS account", f"Recover your account within 30 minutes using this link:\n{os.environ['APP_PUBLIC_URL'].rstrip('/')}/recover-account?token={raw}\n\nIf you did not request this, ignore this message.")
        except HTTPException as exc:
            logger.warning("Account-recovery email delivery failed: %s", exc.detail)
    return {"message": "If the account exists, recovery instructions will be sent."}


@router.post("/recovery/confirm")
def confirm_account_recovery(payload: RecoveryKeyConfirm, request: Request, db: Session = Depends(get_db)):
    """Reset a password using the one-time recovery key issued at signup; no email service required."""
    from app.api.anonymous_sessions import _hash_password
    email = payload.email.strip().lower()
    ip = request.client.host if request.client else "unknown"
    check_security_action_throttle(db, email, ip, "account_recovery")
    account = db.query(CaregiverAccount).filter(CaregiverAccount.email == email).with_for_update().first()
    candidate_hash = _token_hash(payload.recovery_code.strip())
    row = None
    if account is not None:
        lock_clause = " FOR UPDATE" if db.bind is not None and db.bind.dialect.name == "postgresql" else ""
        row = db.execute(text(
            "SELECT id FROM account_recovery_codes WHERE account_id=:account AND code_hash=:hash AND used_at IS NULL" + lock_clause
        ), {"account": account.id, "hash": candidate_hash}).mappings().first()
    if account is None or row is None:
        db.rollback()
        raise HTTPException(status_code=400, detail="Email or recovery key is incorrect, or the key has already been used")
    consumed = db.execute(text(
        "UPDATE account_recovery_codes SET used_at=:now WHERE id=:id AND used_at IS NULL"
    ), {"now": _now(), "id": row["id"]})
    if consumed.rowcount != 1:
        db.rollback()
        raise HTTPException(status_code=400, detail="Recovery key has already been used")
    account.password_hash = _hash_password(payload.new_password)
    account.email_verified_at = _now()
    account.updated_at = _now()
    _revoke_sessions(db, account)
    db.execute(text("UPDATE account_mfa_secrets SET confirmed_at=NULL WHERE account_id=:id"), {"id": account.id})
    account.mfa_enabled = False
    db.execute(text("UPDATE account_mfa_recovery_codes SET used_at=:now WHERE account_id=:id AND used_at IS NULL"), {"now": _now(), "id": account.id})
    db.execute(text("UPDATE account_security_tokens SET consumed_at=:now WHERE account_id=:id AND consumed_at IS NULL"), {"now": _now(), "id": account.id})
    db.commit()
    return {"recovered": True, "sessions_revoked": True, "mfa_reset": True, "recovery_key_consumed": True}


@router.post("/password/change")
def change_password(payload: PasswordChange, request: Request, db: Session = Depends(get_db)):
    """Allow a signed-in user to change their password without SMTP, then revoke sessions."""
    from app.api.anonymous_sessions import _hash_password, _verify_password
    account = _account_from_bearer(request, db)
    if not _verify_password(payload.current_password, account.password_hash):
        raise HTTPException(status_code=401, detail="Current password is incorrect")
    account.password_hash = _hash_password(payload.new_password)
    account.updated_at = _now()
    _revoke_sessions(db, account)
    db.commit()
    return {"password_changed": True, "sessions_revoked": True, "sign_in_required": True}


@router.post("/password/reset-on-device")
def reset_password_on_authenticated_device(payload: DevicePasswordReset, request: Request, db: Session = Depends(get_db)):
    """Use an already-authenticated device session as recovery proof when the password is forgotten."""
    from app.api.anonymous_sessions import _hash_password
    account = _account_from_bearer(request, db)
    account.password_hash = _hash_password(payload.new_password)
    account.updated_at = _now()
    _revoke_sessions(db, account)
    db.commit()
    return {"password_changed": True, "device_session_used_as_recovery_proof": True,
            "sessions_revoked": True, "sign_in_required": True}


@router.get("/mfa/status")
def mfa_status(request: Request, db: Session = Depends(get_db)):
    account = _account_from_bearer(request, db)
    return {"enabled": bool(account.mfa_enabled)}


@router.post("/mfa/enroll")
def enroll_mfa(request: Request, db: Session = Depends(get_db)):
    from app.api.anonymous_sessions import _verify_password
    account = _account_from_bearer(request, db)
    if account.mfa_enabled:
        raise HTTPException(status_code=409, detail="MFA is already enabled; disable it before enrolling a replacement authenticator")
    secret = secrets.token_bytes(20)
    ciphertext = _seal(secret)
    db.execute(text("""INSERT INTO account_mfa_secrets (account_id, secret_ciphertext, confirmed_at, updated_at)
        VALUES (:id, :secret, NULL, :now) ON CONFLICT (account_id) DO UPDATE SET secret_ciphertext=:secret, confirmed_at=NULL, updated_at=:now"""),
        {"id": account.id, "secret": ciphertext, "now": _now()})
    db.commit()
    base32 = base64.b32encode(secret).decode().rstrip("=")
    label = f"SolenOS:{account.email}"
    return {"secret": base32, "otpauth_uri": f"otpauth://totp/{label}?secret={base32}&issuer=SolenOS&algorithm=SHA1&digits=6&period=30", "confirmed": False}


def _account_from_bearer(request: Request, db: Session):
    scheme, _, raw = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not raw.strip():
        raise HTTPException(status_code=401, detail="A valid account session is required")
    token_hash = hashlib.sha256(raw.strip().encode()).hexdigest()
    session = db.query(AnonymousSession).filter(AnonymousSession.token_hash == token_hash, AnonymousSession.revoked_at.is_(None), AnonymousSession.expires_at > _now()).first()
    if not session:
        raise HTTPException(status_code=401, detail="A valid account session is required")
    account = db.query(CaregiverAccount).filter(CaregiverAccount.caregiver_id == session.caregiver_id).first()
    if not account:
        raise HTTPException(status_code=403, detail="This action requires an account")
    return account


@router.post("/mfa/confirm")
def confirm_mfa(payload: MfaConfirm, request: Request, db: Session = Depends(get_db)):
    account = _account_from_bearer(request, db)
    lock_clause = " FOR UPDATE" if db.bind is not None and db.bind.dialect.name == "postgresql" else ""
    row = db.execute(text("SELECT secret_ciphertext, confirmed_at FROM account_mfa_secrets WHERE account_id=:id" + lock_clause), {"id": account.id}).mappings().first()
    if not row:
        raise HTTPException(status_code=400, detail="Start MFA enrollment first")
    try:
        secret = _unseal(row["secret_ciphertext"])
    except Exception as exc:
        raise HTTPException(status_code=503, detail="MFA secret cannot be decrypted; contact support") from exc
    if not _valid_totp(secret, payload.code):
        raise HTTPException(status_code=400, detail="Invalid authenticator code")
    account.mfa_enabled = True
    db.execute(text("UPDATE account_mfa_secrets SET confirmed_at=:now, updated_at=:now WHERE account_id=:id"), {"now": _now(), "id": account.id})
    codes = [secrets.token_urlsafe(9) for _ in range(10)]
    db.execute(text("DELETE FROM account_mfa_recovery_codes WHERE account_id=:id"), {"id": account.id})
    for code in codes:
        db.execute(text("""INSERT INTO account_mfa_recovery_codes (id, account_id, code_hash, created_at)
            VALUES (:id, :account, :hash, :now)"""), {"id": str(uuid.uuid4()), "account": account.id, "hash": _token_hash(code), "now": _now()})
    db.commit()
    return {"mfa_enabled": True, "recovery_codes": codes, "warning": "Store these codes securely. They are shown only once."}


@router.post("/mfa/disable")
def disable_mfa(payload: MfaConfirm, request: Request, db: Session = Depends(get_db)):
    account = _account_from_bearer(request, db)
    row = db.execute(text("SELECT secret_ciphertext FROM account_mfa_secrets WHERE account_id=:id AND confirmed_at IS NOT NULL"), {"id": account.id}).mappings().first()
    if not row:
        raise HTTPException(status_code=400, detail="MFA is not enabled")
    try:
        secret = _unseal(row["secret_ciphertext"])
    except Exception as exc:
        raise HTTPException(status_code=503, detail="MFA secret cannot be decrypted; contact support") from exc
    if not _valid_totp(secret, payload.code):
        raise HTTPException(status_code=400, detail="Invalid authenticator code")
    account.mfa_enabled = False
    db.execute(text("UPDATE account_mfa_secrets SET confirmed_at=NULL, updated_at=:now WHERE account_id=:id"), {"now": _now(), "id": account.id})
    db.execute(text("UPDATE account_mfa_recovery_codes SET used_at=:now WHERE account_id=:id AND used_at IS NULL"), {"now": _now(), "id": account.id})
    db.commit()
    return {"mfa_enabled": False}


def verify_mfa_login(db: Session, account: CaregiverAccount, code: str | None) -> bool:
    if not account.mfa_enabled:
        return True
    if not code:
        return False
    row = db.execute(text("SELECT secret_ciphertext FROM account_mfa_secrets WHERE account_id=:id AND confirmed_at IS NOT NULL"), {"id": account.id}).mappings().first()
    if row:
        try:
            if _valid_totp(_unseal(row["secret_ciphertext"]), code):
                return True
        except Exception:
            return False
    # Recovery codes are single-use and are consumed atomically.
    hashed = _token_hash(code)
    lock_clause = " FOR UPDATE" if db.bind is not None and db.bind.dialect.name == "postgresql" else ""
    recovery = db.execute(text("""SELECT id FROM account_mfa_recovery_codes
        WHERE account_id=:id AND code_hash=:hash AND used_at IS NULL""" + lock_clause), {"id": account.id, "hash": hashed}).mappings().first()
    if recovery:
        db.execute(text("UPDATE account_mfa_recovery_codes SET used_at=:now WHERE id=:id AND used_at IS NULL"), {"now": _now(), "id": recovery["id"]})
        return True
    return False
