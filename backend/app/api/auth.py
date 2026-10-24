import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session
from sqlalchemy import or_
from app.core.config import get_settings
from app.core.database import get_db
from app.models.care import Person, Caregiver
from app.models.auth import AuthAccount
from app.models.collaboration import ProfessionalAccessGrant

router = APIRouter()
settings = get_settings()
PBKDF2_ROUNDS = 310_000

class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=256)
    enrollment_key: str = Field(min_length=1, max_length=512)
    caregiver_id: Optional[str] = None
    person_id: Optional[str] = None
    name: Optional[str] = Field(default=None, max_length=160)
    relationship: Optional[str] = Field(default='Family caregiver', max_length=100)
    person_name: Optional[str] = Field(default=None, max_length=160)

class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)

class ProvisionRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=256)
    caregiver_id: str
    role: str = Field(pattern='^(professional|team)$')

def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode('ascii').rstrip('=')

def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + '=' * (-len(value) % 4))

def _secret() -> bytes:
    secret = settings.secret_key
    if secret == 'change-me-in-production' or len(secret) < 32:
        raise HTTPException(status_code=503, detail='Authentication is unavailable until SECRET_KEY is configured')
    return secret.encode('utf-8')

def _hash_password(password: str, salt: Optional[bytes] = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, PBKDF2_ROUNDS)
    return 'pbkdf2_sha256$' + str(PBKDF2_ROUNDS) + '$' + _b64(salt) + '$' + _b64(digest)

def _verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, rounds, salt, expected = encoded.split('$', 3)
        if scheme != 'pbkdf2_sha256': return False
        actual = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), _unb64(salt), int(rounds))
        return hmac.compare_digest(_b64(actual), expected)
    except (ValueError, TypeError):
        return False

def issue_token(account: AuthAccount) -> str:
    now = int(time.time())
    payload = {'sub': account.id, 'caregiver_id': account.caregiver_id, 'role': account.role, 'iat': now, 'exp': now + max(5, settings.access_token_expire_minutes) * 60}
    encoded = _b64(json.dumps(payload, separators=(',', ':'), sort_keys=True).encode('utf-8'))
    signature = _b64(hmac.new(_secret(), encoded.encode('ascii'), hashlib.sha256).digest())
    return encoded + '.' + signature

def verify_token(token: str) -> dict:
    try:
        encoded, signature = token.split('.', 1)
        expected = _b64(hmac.new(_secret(), encoded.encode('ascii'), hashlib.sha256).digest())
        if not hmac.compare_digest(signature, expected): raise ValueError('signature')
        payload = json.loads(_unb64(encoded).decode('utf-8'))
        if int(payload.get('exp', 0)) <= int(time.time()): raise ValueError('expired')
        if not all(isinstance(payload.get(k), str) and payload[k] for k in ('sub', 'caregiver_id', 'role')): raise ValueError('claims')
        return payload
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=401, detail='Invalid or expired access token') from exc

def require_enrollment_key(value: str) -> None:
    expected = os.getenv('ACCOUNT_ENROLLMENT_KEY', '')
    if len(expected) < 24: raise HTTPException(status_code=503, detail='Account enrollment is not configured by the service administrator')
    if not hmac.compare_digest(value, expected): raise HTTPException(status_code=403, detail='Invalid enrollment key')

@router.post('/auth/register', status_code=201)
def register(payload: RegisterRequest, db: Session = Depends(get_db)):
    require_enrollment_key(payload.enrollment_key)
    email = payload.email.lower().strip()
    if db.query(AuthAccount).filter(AuthAccount.email == email).first(): raise HTTPException(status_code=409, detail='An account already exists for this email')
    caregiver = None
    if payload.caregiver_id:
        caregiver = db.query(Caregiver).filter(Caregiver.id == payload.caregiver_id).first()
        if not caregiver or caregiver.email.lower() != email: raise HTTPException(status_code=403, detail='Caregiver ID and email must match an existing caregiver record')
        if payload.person_id and payload.person_id != caregiver.person_id: raise HTTPException(status_code=403, detail='Care recipient does not match caregiver record')
    else:
        if not payload.name or not payload.person_name: raise HTTPException(status_code=422, detail='For a new care record, name and person_name are required')
        person = Person(name=payload.person_name.strip()); db.add(person); db.flush()
        caregiver = Caregiver(name=payload.name.strip(), email=email, relationship=payload.relationship or 'Family caregiver', person_id=person.id)
        db.add(caregiver); db.flush()
    account = AuthAccount(caregiver_id=caregiver.id, email=email, password_hash=_hash_password(payload.password), role='caregiver', is_active=True)
    db.add(account); db.commit(); db.refresh(account)
    token = issue_token(account)
    return {'access_token': token, 'token_type': 'bearer', 'expires_in': settings.access_token_expire_minutes * 60, 'account': {'id': account.id, 'email': account.email, 'role': account.role, 'caregiver_id': account.caregiver_id, 'person_id': caregiver.person_id}}

@router.post('/auth/login')
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    account = db.query(AuthAccount).filter(AuthAccount.email == payload.email.lower().strip()).first()
    if not account or not account.is_active or not _verify_password(payload.password, account.password_hash): raise HTTPException(status_code=401, detail='Email or password is incorrect')
    account.last_login_at = datetime.now(timezone.utc); db.commit()
    caregiver = db.query(Caregiver).filter(Caregiver.id == account.caregiver_id).first()
    if not caregiver: raise HTTPException(status_code=403, detail='Account is not linked to an active caregiver record')
    return {'access_token': issue_token(account), 'token_type': 'bearer', 'expires_in': settings.access_token_expire_minutes * 60, 'account': {'id': account.id, 'email': account.email, 'role': account.role, 'caregiver_id': account.caregiver_id, 'person_id': caregiver.person_id}}

@router.post('/auth/provision')
def provision_role(payload: ProvisionRequest, x_solenos_admin_key: Optional[str] = Header(default=None), db: Session = Depends(get_db)):
    expected = os.getenv('ACCOUNT_ADMIN_KEY', '')
    if len(expected) < 32 or not x_solenos_admin_key or not hmac.compare_digest(expected, x_solenos_admin_key): raise HTTPException(status_code=403, detail='Administrator provisioning is not authorized')
    caregiver = db.query(Caregiver).filter(Caregiver.id == payload.caregiver_id).first()
    if not caregiver or caregiver.email.lower() != payload.email.lower(): raise HTTPException(status_code=403, detail='Email must match the existing caregiver record')
    account = db.query(AuthAccount).filter(AuthAccount.email == payload.email.lower()).first()
    if account:
        account.password_hash = _hash_password(payload.password); account.role = payload.role; account.caregiver_id = caregiver.id; account.is_active = True
    else:
        account = AuthAccount(caregiver_id=caregiver.id, email=payload.email.lower(), password_hash=_hash_password(payload.password), role=payload.role, is_active=True); db.add(account)
    db.commit(); db.refresh(account)
    return {'id': account.id, 'email': account.email, 'role': account.role, 'caregiver_id': account.caregiver_id, 'active': account.is_active}


def _authenticated_account(authorization: Optional[str], db: Session):
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Bearer token required")
    claims = verify_token(authorization.split(" ", 1)[1].strip())
    account = db.query(AuthAccount).filter_by(id=claims["sub"], is_active=True).first()
    if (
        not account
        or account.caregiver_id != claims.get("caregiver_id")
        or account.role != claims.get("role")
    ):
        raise HTTPException(status_code=401, detail="Account is inactive or token claims are stale")
    caregiver = db.query(Caregiver).filter_by(id=account.caregiver_id).first()
    if not caregiver:
        raise HTTPException(status_code=401, detail="Account caregiver is unavailable")
    return account, caregiver


@router.get("/auth/care-contexts")
def list_care_contexts(
    authorization: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
):
    """Return only care recipients authorized for the authenticated account."""
    account, caregiver = _authenticated_account(authorization, db)
    if account.role == "team":
        return []

    if account.role == "caregiver":
        person = db.query(Person).filter_by(id=caregiver.person_id).first()
        if not person:
            raise HTTPException(status_code=409, detail="The linked care recipient is unavailable")
        return [{
            "person_id": person.id,
            "name": person.name,
            "role": "caregiver",
            "permissions": ["read", "write"],
        }]

    now = datetime.now(timezone.utc)
    grants = (
        db.query(ProfessionalAccessGrant, Person)
        .join(Person, Person.id == ProfessionalAccessGrant.person_id)
        .filter(
            ProfessionalAccessGrant.professional_caregiver_id == caregiver.id,
            ProfessionalAccessGrant.status == "active",
            or_(ProfessionalAccessGrant.expires_at.is_(None), ProfessionalAccessGrant.expires_at > now),
        )
        .order_by(Person.name.asc())
        .all()
    )
    return [{
        "person_id": person.id,
        "name": person.name,
        "role": "professional",
        "permissions": ["read", "write"],
    } for _grant, person in grants]


@router.get("/auth/my-caregiver-links")
def list_my_caregiver_links(
    person_id: str,
    authorization: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
):
    """Return the caller's own caregiver/account link only after recipient access is verified."""
    account, caregiver = _authenticated_account(authorization, db)
    if account.role == "team":
        raise HTTPException(status_code=403, detail="Team accounts do not have caregiver links")
    if account.role == "caregiver":
        allowed = caregiver.person_id == person_id
    else:
        now = datetime.now(timezone.utc)
        allowed = db.query(ProfessionalAccessGrant.id).filter(
            ProfessionalAccessGrant.professional_caregiver_id == caregiver.id,
            ProfessionalAccessGrant.person_id == person_id,
            ProfessionalAccessGrant.status == "active",
            or_(ProfessionalAccessGrant.expires_at.is_(None), ProfessionalAccessGrant.expires_at > now),
        ).first() is not None
    if not allowed:
        raise HTTPException(status_code=403, detail="This account is not authorized for the selected care recipient")
    return [{
        "person_id": person_id,
        "caregiver_id": caregiver.id,
        "account_id": account.id,
        "permissions": ["read", "write"],
    }]

@router.get('/auth/me')
def who_am_i(authorization: Optional[str] = Header(default=None), db: Session = Depends(get_db)):
    if not authorization or not authorization.lower().startswith('bearer '): raise HTTPException(status_code=401, detail='Bearer token required')
    claims = verify_token(authorization.split(' ', 1)[1].strip())
    account = db.query(AuthAccount).filter_by(id=claims['sub'], is_active=True).first()
    if not account: raise HTTPException(status_code=401, detail='Account is inactive or no longer exists')
    caregiver = db.query(Caregiver).filter_by(id=account.caregiver_id).first()
    if not caregiver: raise HTTPException(status_code=401, detail='Account caregiver is unavailable')
    return {'authenticated': True, 'account_id': account.id, 'email': account.email, 'role': account.role, 'caregiver_id': caregiver.id, 'person_id': caregiver.person_id}