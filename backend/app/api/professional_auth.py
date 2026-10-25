import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.models.professional_account import ProfessionalAccount, ProfessionalSession

router = APIRouter(prefix="/professional-auth", tags=["professional accounts"])
SESSION_TTL_DAYS = 7
_EMAIL_RE = re.compile(r"^[^\\s@]+@[^\\s@]+\\.[^\\s@]+$")

class ProfessionalRegister(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=10, max_length=128)
    full_name: str = Field(min_length=2, max_length=160)
    professional_title: str = Field(min_length=2, max_length=160)
    organization: str | None = Field(default=None, max_length=200)
    credentials: str | None = Field(default=None, max_length=300)
    specialties: str | None = Field(default=None, max_length=1000)
    biography: str | None = Field(default=None, max_length=3000)

class ProfessionalLogin(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=128)

def _email(value: str) -> str:
    value = value.strip().lower()
    if not _EMAIL_RE.fullmatch(value):
        raise HTTPException(status_code=422, detail="Enter a valid email address")
    return value

def _hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=64)
    return "scrypt$16384$8$1$" + salt.hex() + "$" + derived.hex()

def _verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt_hex, expected_hex = encoded.split("$", 5)
        if algorithm != "scrypt":
            return False
        actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), n=int(n), r=int(r), p=int(p), dklen=len(bytes.fromhex(expected_hex)))
        return hmac.compare_digest(actual.hex(), expected_hex)
    except (ValueError, TypeError):
        return False

def _public_profile(account: ProfessionalAccount) -> dict:
    return {
        "id": account.id, "email": account.email, "full_name": account.full_name,
        "professional_title": account.professional_title, "organization": account.organization,
        "credentials": account.credentials, "specialties": account.specialties,
        "biography": account.biography, "status": account.status,
        "created_at": account.created_at.isoformat() if account.created_at else None,
    }

def _issue_session(db: Session, account: ProfessionalAccount) -> dict:
    raw = secrets.token_urlsafe(32)
    expiry = datetime.now(timezone.utc) + timedelta(days=SESSION_TTL_DAYS)
    db.add(ProfessionalSession(professional_id=account.id, token_hash=hashlib.sha256(raw.encode()).hexdigest(), expires_at=expiry))
    db.commit()
    return {"professional": _public_profile(account), "access_token": raw, "token_type": "Bearer",
            "expires_at": expiry.isoformat(), "expires_in_seconds": SESSION_TTL_DAYS * 86400,
            "session_type": "professional"}

def _current(request: Request, db: Session) -> tuple[ProfessionalSession, ProfessionalAccount]:
    scheme, _, raw = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not raw.strip():
        raise HTTPException(status_code=401, detail="A valid professional session is required")
    token_hash = hashlib.sha256(raw.strip().encode()).hexdigest()
    session = db.query(ProfessionalSession).filter(
        ProfessionalSession.token_hash == token_hash,
        ProfessionalSession.revoked_at.is_(None),
        ProfessionalSession.expires_at > datetime.now(timezone.utc),
    ).first()
    if session is None:
        raise HTTPException(status_code=401, detail="Professional session is invalid or expired")
    account = db.query(ProfessionalAccount).filter(ProfessionalAccount.id == session.professional_id).first()
    if account is None:
        raise HTTPException(status_code=401, detail="Professional account is no longer available")
    return session, account

@router.post("/register", status_code=201)
def register(payload: ProfessionalRegister, db: Session = Depends(get_db)):
    email = _email(payload.email)
    if db.query(ProfessionalAccount.id).filter(ProfessionalAccount.email == email).first():
        raise HTTPException(status_code=409, detail="An account with this email already exists. Log in instead.")
    name, title = payload.full_name.strip(), payload.professional_title.strip()
    if not name or not title:
        raise HTTPException(status_code=422, detail="Name and professional title are required")
    account = ProfessionalAccount(
        email=email, password_hash=_hash_password(payload.password), full_name=name,
        professional_title=title, organization=(payload.organization or "").strip() or None,
        credentials=(payload.credentials or "").strip() or None,
        specialties=(payload.specialties or "").strip() or None,
        biography=(payload.biography or "").strip() or None, status="pending_review")
    try:
        db.add(account)
        db.flush()
        result = _issue_session(db, account)
        result["message"] = "Your professional account is created. Your profile is pending review; publishing and service features remain unavailable until approval."
        return result
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="An account with this email already exists. Log in instead.")

@router.post("/login")
def login(payload: ProfessionalLogin, db: Session = Depends(get_db)):
    account = db.query(ProfessionalAccount).filter(ProfessionalAccount.email == _email(payload.email)).first()
    if account is None or not _verify_password(payload.password, account.password_hash):
        raise HTTPException(status_code=401, detail="Email or password is incorrect")
    return _issue_session(db, account)

@router.get("/me")
def me(request: Request, db: Session = Depends(get_db)):
    _, account = _current(request, db)
    return {"authenticated": True, "session_type": "professional", "professional": _public_profile(account)}

@router.post("/logout")
def logout(request: Request, db: Session = Depends(get_db)):
    session, _ = _current(request, db)
    session.revoked_at = datetime.now(timezone.utc)
    db.commit()
    return {"revoked": True}

@router.delete("/account")
def delete_account(request: Request, db: Session = Depends(get_db)):
    _, account = _current(request, db)
    db.query(ProfessionalSession).filter(ProfessionalSession.professional_id == account.id).delete(synchronize_session=False)
    db.delete(account)
    db.commit()
    return {"deleted": True}
