import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.config import get_settings
from app.core.security import hash_password, issue_access_token, verify_password
from app.models.auth import Account, CareInvitation, CareMembership
from app.models.care import Caregiver, Person

router = APIRouter(prefix="/auth", tags=["authentication"])


class SignupInput(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=1024)
    display_name: str | None = Field(default=None, max_length=120)


class LoginInput(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=1024)


class CareContextInput(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    caregiver_name: str | None = Field(default=None, max_length=120)
    relationship: str = Field(default="family caregiver", min_length=1, max_length=120)


class InvitationCreateInput(BaseModel):
    person_id: str = Field(min_length=1, max_length=128)
    caregiver_id: str = Field(min_length=1, max_length=128)
    permissions: list[str] = Field(default_factory=lambda: ["read", "write"])

    @field_validator("permissions")
    @classmethod
    def validate_permissions(cls, value: list[str]) -> list[str]:
        allowed = {"read", "write"}
        normalized = list(dict.fromkeys(value))
        if not normalized or "read" not in normalized or not set(normalized).issubset(allowed):
            raise ValueError("Invitation permissions must include read and may include write.")
        return normalized


class InvitationAcceptInput(BaseModel):
    token: str = Field(min_length=24, max_length=256)


def _account_payload(account: Account) -> dict[str, Any]:
    return {"id": account.id, "email": account.email, "display_name": account.display_name}


def _bearer_token(request: Request) -> str:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(status_code=401, detail="Sign in to continue.")
    return token.strip()


def current_account(request: Request, db: Session = Depends(get_db)) -> Account:
    from jose import JWTError
    from app.core.security import decode_access_token

    try:
        claims = decode_access_token(_bearer_token(request))
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except JWTError as exc:
        raise HTTPException(status_code=401, detail="Session is invalid or expired.") from exc

    account_id = claims.get("sub")
    if not isinstance(account_id, str) or not account_id:
        raise HTTPException(status_code=401, detail="Session is invalid.")
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account or not account.is_active or claims.get("ver") != account.token_version:
        raise HTTPException(status_code=401, detail="Session is no longer active.")
    return account


@router.post("/signup", status_code=status.HTTP_201_CREATED)
def signup(payload: SignupInput, db: Session = Depends(get_db)):
    settings = get_settings()
    if not settings.secret_key or settings.secret_key == "change-me-in-production":
        raise HTTPException(status_code=503, detail="Authentication is disabled until a unique SECRET_KEY is configured.")
    email = str(payload.email).strip().lower()
    if db.query(Account).filter(Account.email == email).first():
        # Avoid disclosing whether an account exists.
        raise HTTPException(status_code=409, detail="An account with this email cannot be created.")
    try:
        password_hash = hash_password(payload.password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    account = Account(email=email, password_hash=password_hash, display_name=(payload.display_name or "").strip() or None)
    db.add(account)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=409, detail="Could not create this account. Check the details and retry.")
    db.refresh(account)
    return {"account": _account_payload(account)}


@router.post("/login")
def login(payload: LoginInput, db: Session = Depends(get_db)):
    email = str(payload.email).strip().lower()
    account = db.query(Account).filter(Account.email == email).first()
    # Use the same response for unknown email and wrong password.
    if not account or not account.is_active or not verify_password(payload.password, account.password_hash):
        raise HTTPException(status_code=401, detail="Email or password is incorrect.")
    try:
        token, expires_at = issue_access_token(account.id, account.token_version)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_at": expires_at.isoformat(),
        "account": _account_payload(account),
    }


@router.get("/me")
def me(account: Account = Depends(current_account)):
    return {"account": _account_payload(account)}


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(account: Account = Depends(current_account), db: Session = Depends(get_db)):
    # Incrementing the version revokes every previously issued token for this account.
    account.token_version = str(int(account.token_version or "0") + 1)
    db.add(account)
    db.commit()
    return None


@router.get("/care-contexts")
def list_care_contexts(account: Account = Depends(current_account), db: Session = Depends(get_db)):
    rows = (
        db.query(CareMembership, Person)
        .join(Person, Person.id == CareMembership.person_id)
        .filter(
            CareMembership.account_id == account.id,
            CareMembership.status == "active",
        )
        .order_by(Person.created_at.desc())
        .all()
    )
    return [
        {
            "person_id": membership.person_id,
            "name": person.name,
            "role": membership.role,
            "permissions": json.loads(membership.permissions),
        }
        for membership, person in rows
    ]


@router.post("/care-contexts", status_code=status.HTTP_201_CREATED)
def create_care_context(
    payload: CareContextInput,
    account: Account = Depends(current_account),
    db: Session = Depends(get_db),
):
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Care record name cannot be blank.")
    # A care record and its first caregiver membership are created atomically.
    person = Person(name=name)
    db.add(person)
    db.flush()
    caregiver_name = (payload.caregiver_name or account.display_name or account.email.split("@")[0]).strip()
    relationship = payload.relationship.strip()
    if not caregiver_name or not relationship:
        raise HTTPException(status_code=422, detail="Caregiver name and relationship cannot be blank.")
    caregiver = Caregiver(
        name=caregiver_name,
        email=account.email,
        relationship=relationship,
        person_id=person.id,
        timezone="UTC",
    )
    db.add(caregiver)
    db.flush()
    membership = CareMembership(
        account_id=account.id,
        person_id=person.id,
        caregiver_id=caregiver.id,
        role="caregiver",
        permissions=json.dumps(["read", "write"]),
        status="active",
    )
    db.add(membership)
    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Could not create the care record. Retry or contact support.") from exc
    db.refresh(person)
    return {
        "person_id": person.id,
        "name": person.name,
        "role": "caregiver",
        "permissions": ["read", "write"],
    }


@router.get("/my-caregiver-links")
def my_caregiver_links(
    person_id: str,
    account: Account = Depends(current_account),
    db: Session = Depends(get_db),
):
    rows = db.query(CareMembership).filter(
        CareMembership.account_id == account.id,
        CareMembership.person_id == person_id,
        CareMembership.status == "active",
    ).all()
    return [
        {
            "person_id": row.person_id,
            "caregiver_id": row.caregiver_id,
            "account_id": row.account_id,
            "permissions": json.loads(row.permissions),
        }
        for row in rows
    ]


@router.post("/caregiver-invitations", status_code=status.HTTP_201_CREATED)
def create_caregiver_invitation(
    payload: InvitationCreateInput,
    account: Account = Depends(current_account),
    db: Session = Depends(get_db),
):
    inviter_membership = db.query(CareMembership).filter(
        CareMembership.account_id == account.id,
        CareMembership.person_id == payload.person_id,
        CareMembership.status == "active",
    ).first()
    if not inviter_membership:
        raise HTTPException(status_code=403, detail="You are not a member of this care circle.")
    try:
        inviter_permissions = set(json.loads(inviter_membership.permissions))
    except (TypeError, json.JSONDecodeError):
        raise HTTPException(status_code=403, detail="Care-circle permissions are invalid.")
    if "write" not in inviter_permissions:
        raise HTTPException(status_code=403, detail="You do not have permission to invite caregivers.")
    if not set(payload.permissions).issubset(inviter_permissions):
        raise HTTPException(status_code=403, detail="An invitation cannot grant permissions you do not have.")

    caregiver = db.query(Caregiver).filter(
        Caregiver.id == payload.caregiver_id,
        Caregiver.person_id == payload.person_id,
    ).first()
    if not caregiver:
        raise HTTPException(status_code=404, detail="Caregiver is not linked to this care record.")
    if not caregiver.email or "@" not in caregiver.email:
        raise HTTPException(status_code=400, detail="The caregiver must have a valid email address.")

    existing_account = db.query(Account).filter(Account.email == caregiver.email.strip().lower()).first()
    if existing_account:
        existing_membership = db.query(CareMembership).filter(
            CareMembership.account_id == existing_account.id,
            CareMembership.person_id == payload.person_id,
            CareMembership.status == "active",
        ).first()
        if existing_membership:
            raise HTTPException(status_code=409, detail="This caregiver already has access to the care record.")

    raw_token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(days=7)
    invitation = CareInvitation(
        token_hash=hashlib.sha256(raw_token.encode("utf-8")).hexdigest(),
        email=caregiver.email.strip().lower(),
        person_id=payload.person_id,
        caregiver_id=caregiver.id,
        invited_by_account_id=account.id,
        permissions=json.dumps(payload.permissions),
        status="pending",
        expires_at=expires_at,
    )
    db.add(invitation)
    db.commit()
    return {
        "invitation_token": raw_token,
        "caregiver_id": caregiver.id,
        "person_id": payload.person_id,
        "permissions": payload.permissions,
        "expires_at": expires_at.isoformat(),
    }


@router.post("/caregiver-invitations/accept")
def accept_caregiver_invitation(
    payload: InvitationAcceptInput,
    account: Account = Depends(current_account),
    db: Session = Depends(get_db),
):
    token_hash = hashlib.sha256(payload.token.encode("utf-8")).hexdigest()
    invitation = db.query(CareInvitation).filter(CareInvitation.token_hash == token_hash).first()
    if not invitation or invitation.status != "pending":
        raise HTTPException(status_code=404, detail="Invitation is invalid or has already been used.")

    expires_at = invitation.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= datetime.now(timezone.utc):
        invitation.status = "expired"
        db.commit()
        raise HTTPException(status_code=410, detail="Invitation has expired. Ask the care-circle owner to issue a new one.")

    if account.email.strip().lower() != invitation.email.strip().lower():
        raise HTTPException(status_code=403, detail="This invitation was issued to a different email address.")

    caregiver = db.query(Caregiver).filter(
        Caregiver.id == invitation.caregiver_id,
        Caregiver.person_id == invitation.person_id,
    ).first()
    if not caregiver or caregiver.email.strip().lower() != account.email.strip().lower():
        raise HTTPException(status_code=409, detail="The invitation no longer matches this caregiver record.")

    existing = db.query(CareMembership).filter(
        CareMembership.account_id == account.id,
        CareMembership.person_id == invitation.person_id,
        CareMembership.status == "active",
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail="This account already has access to the care record.")

    membership = CareMembership(
        account_id=account.id,
        person_id=invitation.person_id,
        caregiver_id=caregiver.id,
        role="caregiver",
        permissions=invitation.permissions,
        status="active",
    )
    invitation.status = "accepted"
    invitation.accepted_at = datetime.now(timezone.utc)
    db.add(membership)
    db.add(invitation)
    db.commit()
    person = db.query(Person).filter(Person.id == invitation.person_id).first()
    return {
        "person_id": invitation.person_id,
        "caregiver_id": caregiver.id,
        "name": person.name if person else "Care record",
        "role": "caregiver",
        "permissions": json.loads(invitation.permissions),
    }
