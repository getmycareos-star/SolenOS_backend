"""Account authentication endpoints; account creation does not grant care-record access."""
from typing import Optional
from datetime import datetime, timedelta, timezone
import hashlib
import secrets
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.models.auth_identity import AccountPersonAccess, AuthAccount, CaregiverAccountLink, CaregiverInvitation, CaregiverProfileAccountLink, CaregiverProfileInvitation
from app.models.care import Person, Caregiver
from app.models.caregiver_intelligence import CaregiverProfile
from app.services.auth_service import authenticate_account, create_account, create_session, resolve_session, revoke_session

router = APIRouter(prefix="/auth", tags=["authentication"])
bearer = HTTPBearer(auto_error=False)

class SignupInput(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=12, max_length=1024)
    display_name: Optional[str] = Field(default=None, max_length=120)
    @field_validator("email")
    @classmethod
    def validate_email_shape(cls, value: str) -> str:
        normalized = value.strip().lower()
        if "@" not in normalized or normalized.startswith("@") or normalized.endswith("@"):
            raise ValueError("A valid email address is required.")
        return normalized

class LoginInput(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=1024)

class CareContextInput(BaseModel):
    name: str = Field(min_length=1, max_length=200)

class CareContextOutput(BaseModel):
    person_id: str
    name: str
    role: str
    permissions: list[str]

class CaregiverInvitationInput(BaseModel):
    person_id: str
    caregiver_id: str
    permissions: list[str] = Field(default_factory=lambda: ["read", "write"])

    @field_validator("permissions")
    @classmethod
    def validate_permissions(cls, value: list[str]) -> list[str]:
        normalized = sorted(set(value))
        if not normalized or any(item not in {"read", "write"} for item in normalized):
            raise ValueError("Invitation permissions may contain read and/or write only")
        return normalized

class AcceptCaregiverInvitationInput(BaseModel):
    token: str = Field(min_length=20, max_length=256)

class CaregiverInvitationOutput(BaseModel):
    invitation_token: str
    person_id: str
    caregiver_id: str
    permissions: list[str]
    expires_at: str


class CaregiverProfileInvitationInput(BaseModel):
    person_id: str
    caregiver_profile_id: str
    permissions: list[str] = Field(default_factory=lambda: ["read", "write"])

    @field_validator("permissions")
    @classmethod
    def validate_permissions(cls, value: list[str]) -> list[str]:
        normalized = sorted(set(value))
        if not normalized or any(item not in {"read", "write"} for item in normalized):
            raise ValueError("Invitation permissions may contain read and/or write only")
        return normalized

class AcceptCaregiverProfileInvitationInput(BaseModel):
    token: str = Field(min_length=20, max_length=256)

class CaregiverProfileInvitationOutput(BaseModel):
    invitation_token: str
    person_id: str
    caregiver_profile_id: str
    permissions: list[str]
    expires_at: str

class CaregiverProfileLinkOutput(BaseModel):
    person_id: str
    caregiver_profile_id: str
    account_id: str
    permissions: list[str]

class CaregiverLinkOutput(BaseModel):
    person_id: str
    caregiver_id: str
    account_id: str
    permissions: list[str]

class MyCaregiverLinkOutput(BaseModel):
    person_id: str
    caregiver_id: str
    caregiver_name: str
    timezone: str

class AccountOutput(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    email: str
    display_name: Optional[str] = None

def current_account(credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer), db: Session = Depends(get_db)) -> AuthAccount:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="Authentication required", headers={"WWW-Authenticate": "Bearer"})
    account = resolve_session(db, credentials.credentials)
    if account is None:
        raise HTTPException(status_code=401, detail="Invalid or expired session", headers={"WWW-Authenticate": "Bearer"})
    return account

@router.post("/signup", response_model=AccountOutput, status_code=status.HTTP_201_CREATED)
def signup(payload: SignupInput, db: Session = Depends(get_db)):
    try:
        return create_account(db, payload.email, payload.password, payload.display_name)
    except ValueError as exc:
        if "already exists" in str(exc):
            raise HTTPException(status_code=409, detail="Account could not be created.")
        raise HTTPException(status_code=422, detail=str(exc))

@router.post("/login")
def login(payload: LoginInput, db: Session = Depends(get_db)):
    account = authenticate_account(db, payload.email, payload.password)
    if account is None:
        raise HTTPException(status_code=401, detail="Invalid email or password", headers={"WWW-Authenticate": "Bearer"})
    token, session = create_session(db, account)
    return {"access_token": token, "token_type": "bearer", "expires_at": session.expires_at.isoformat(), "account": AccountOutput.model_validate(account).model_dump()}

@router.get("/me", response_model=AccountOutput)
def me(account: AuthAccount = Depends(current_account)):
    return account

@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer), db: Session = Depends(get_db)):
    if credentials is not None and credentials.scheme.lower() == "bearer":
        revoke_session(db, credentials.credentials)
    return None


@router.post("/care-contexts", response_model=CareContextOutput, status_code=status.HTTP_201_CREATED)
def create_care_context(
    payload: CareContextInput,
    account: AuthAccount = Depends(current_account),
    db: Session = Depends(get_db),
):
    """Create a care record and explicitly grant its creator owner permissions."""
    person = Person(name=payload.name.strip())
    if not person.name:
        raise HTTPException(status_code=422, detail="Name is required")
    db.add(person)
    db.flush()

    # The account that creates a care context is the first caregiver for it.
    # Create the caregiver identity and an explicit account link together with
    # the owner grant; otherwise every protected capture write would fail closed
    # because no verified caregiver identity exists yet.
    caregiver_name = (account.display_name or account.email.split("@", 1)[0]).strip()
    caregiver = Caregiver(
        name=caregiver_name or account.email,
        email=account.email,
        relationship="family",
        person_id=person.id,
        timezone="UTC",
    )
    db.add(caregiver)
    db.flush()

    grant = AccountPersonAccess(
        account_id=account.id,
        person_id=person.id,
        role="owner",
        permissions=["read", "write", "admin"],
        is_active=True,
        granted_by_account_id=account.id,
    )
    caregiver_link = CaregiverAccountLink(
        account_id=account.id,
        caregiver_id=caregiver.id,
        person_id=person.id,
        linked_by_account_id=account.id,
        is_active=True,
    )
    db.add(grant)
    db.add(caregiver_link)
    db.commit()
    db.refresh(person)
    return CareContextOutput(
        person_id=person.id,
        name=person.name,
        role=grant.role,
        permissions=grant.permissions,
    )


@router.get("/care-contexts", response_model=list[CareContextOutput])
def list_care_contexts(
    account: AuthAccount = Depends(current_account),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(AccountPersonAccess, Person)
        .join(Person, AccountPersonAccess.person_id == Person.id)
        .filter(
            AccountPersonAccess.account_id == account.id,
            AccountPersonAccess.is_active.is_(True),
            AccountPersonAccess.revoked_at.is_(None),
        )
        .all()
    )
    return [
        CareContextOutput(
            person_id=person.id,
            name=person.name,
            role=grant.role,
            permissions=grant.permissions or [],
        )
        for grant, person in rows
    ]


@router.get("/my-caregiver-links", response_model=list[MyCaregiverLinkOutput])
def my_caregiver_links(
    account: AuthAccount = Depends(current_account),
    db: Session = Depends(get_db),
):
    """Return only caregiver identities explicitly linked to the authenticated account."""
    rows = (
        db.query(CaregiverAccountLink, Caregiver)
        .join(Caregiver, Caregiver.id == CaregiverAccountLink.caregiver_id)
        .filter(
            CaregiverAccountLink.account_id == account.id,
            CaregiverAccountLink.is_active.is_(True),
            CaregiverAccountLink.revoked_at.is_(None),
        )
        .all()
    )
    return [
        MyCaregiverLinkOutput(
            person_id=link.person_id,
            caregiver_id=caregiver.id,
            caregiver_name=caregiver.name,
            timezone=caregiver.timezone or "UTC",
        )
        for link, caregiver in rows
    ]


@router.post("/caregiver-invitations", response_model=CaregiverInvitationOutput, status_code=status.HTTP_201_CREATED)
def create_caregiver_invitation(
    payload: CaregiverInvitationInput,
    account: AuthAccount = Depends(current_account),
    db: Session = Depends(get_db),
):
    """Owner/admin creates a single-use token to link a real account to a caregiver profile."""
    from app.core.authz import assert_person_access
    assert_person_access(db, account.id, payload.person_id, "admin")
    caregiver = db.query(Caregiver).filter(
        Caregiver.id == payload.caregiver_id,
        Caregiver.person_id == payload.person_id,
    ).first()
    if caregiver is None:
        raise HTTPException(status_code=404, detail="Caregiver not found in this care record")
    existing_link = db.query(CaregiverAccountLink).filter(
        CaregiverAccountLink.caregiver_id == caregiver.id,
        CaregiverAccountLink.is_active.is_(True),
        CaregiverAccountLink.revoked_at.is_(None),
    ).first()
    if existing_link is not None:
        raise HTTPException(status_code=409, detail="Caregiver already has an active account link")
    raw_token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(days=7)
    invitation = CaregiverInvitation(
        token_digest=hashlib.sha256(raw_token.encode("utf-8")).hexdigest(),
        person_id=payload.person_id,
        caregiver_id=caregiver.id,
        invited_by_account_id=account.id,
        permissions=payload.permissions,
        expires_at=expires_at,
    )
    db.add(invitation)
    db.commit()
    return CaregiverInvitationOutput(
        invitation_token=raw_token,
        person_id=payload.person_id,
        caregiver_id=caregiver.id,
        permissions=payload.permissions,
        expires_at=expires_at.isoformat(),
    )


@router.post("/caregiver-invitations/accept", response_model=CaregiverLinkOutput)
def accept_caregiver_invitation(
    payload: AcceptCaregiverInvitationInput,
    account: AuthAccount = Depends(current_account),
    db: Session = Depends(get_db),
):
    """Accept an invitation by proving possession of its single-use token."""
    digest = hashlib.sha256(payload.token.encode("utf-8")).hexdigest()
    invitation = db.query(CaregiverInvitation).filter(
        CaregiverInvitation.token_digest == digest,
        CaregiverInvitation.accepted_at.is_(None),
        CaregiverInvitation.revoked_at.is_(None),
    ).first()
    if invitation is None:
        raise HTTPException(status_code=400, detail="Invitation is invalid, expired, or already used")
    expires_at = invitation.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="Invitation is invalid, expired, or already used")
    caregiver = db.query(Caregiver).filter(
        Caregiver.id == invitation.caregiver_id,
        Caregiver.person_id == invitation.person_id,
    ).first()
    if caregiver is None:
        raise HTTPException(status_code=400, detail="Invitation no longer refers to a valid caregiver")
    existing_link = db.query(CaregiverAccountLink).filter(
        CaregiverAccountLink.caregiver_id == caregiver.id,
        CaregiverAccountLink.is_active.is_(True),
        CaregiverAccountLink.revoked_at.is_(None),
    ).first()
    if existing_link is not None:
        raise HTTPException(status_code=409, detail="Caregiver already has an active account link")
    grant = db.query(AccountPersonAccess).filter(
        AccountPersonAccess.account_id == account.id,
        AccountPersonAccess.person_id == invitation.person_id,
    ).first()
    if grant is None:
        grant = AccountPersonAccess(
            account_id=account.id,
            person_id=invitation.person_id,
            role="caregiver",
            permissions=invitation.permissions,
            is_active=True,
            granted_by_account_id=invitation.invited_by_account_id,
        )
        db.add(grant)
    elif grant.is_active and grant.revoked_at is None:
        grant.permissions = sorted(set((grant.permissions or []) + invitation.permissions))
    else:
        grant.role = "caregiver"
        grant.permissions = invitation.permissions
        grant.is_active = True
        grant.revoked_at = None
        grant.granted_by_account_id = invitation.invited_by_account_id
    link = CaregiverAccountLink(
        account_id=account.id,
        caregiver_id=caregiver.id,
        person_id=invitation.person_id,
        linked_by_account_id=invitation.invited_by_account_id,
        is_active=True,
    )
    db.add(link)
    invitation.accepted_by_account_id = account.id
    invitation.accepted_at = datetime.now(timezone.utc)
    db.commit()
    return CaregiverLinkOutput(
        person_id=invitation.person_id,
        caregiver_id=caregiver.id,
        account_id=account.id,
        permissions=grant.permissions or [],
    )


@router.post("/caregiver-profile-invitations", response_model=CaregiverProfileInvitationOutput, status_code=status.HTTP_201_CREATED)
def create_caregiver_profile_invitation(
    payload: CaregiverProfileInvitationInput,
    account: AuthAccount = Depends(current_account),
    db: Session = Depends(get_db),
):
    """Owner/admin invites an account to act under a caregiver-intelligence profile."""
    from app.core.authz import assert_person_access
    assert_person_access(db, account.id, payload.person_id, "admin")
    profile = db.query(CaregiverProfile).filter(
        CaregiverProfile.id == payload.caregiver_profile_id,
        CaregiverProfile.person_id == payload.person_id,
    ).first()
    if profile is None:
        raise HTTPException(status_code=404, detail="Caregiver profile not found in this care record")
    existing_link = db.query(CaregiverProfileAccountLink).filter(
        CaregiverProfileAccountLink.caregiver_profile_id == profile.id,
        CaregiverProfileAccountLink.is_active.is_(True),
        CaregiverProfileAccountLink.revoked_at.is_(None),
    ).first()
    if existing_link is not None:
        raise HTTPException(status_code=409, detail="Caregiver profile already has an active account link")
    raw_token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(days=7)
    invitation = CaregiverProfileInvitation(
        token_digest=hashlib.sha256(raw_token.encode("utf-8")).hexdigest(),
        person_id=payload.person_id,
        caregiver_profile_id=profile.id,
        invited_by_account_id=account.id,
        permissions=payload.permissions,
        expires_at=expires_at,
    )
    db.add(invitation)
    db.commit()
    return CaregiverProfileInvitationOutput(
        invitation_token=raw_token,
        person_id=payload.person_id,
        caregiver_profile_id=profile.id,
        permissions=payload.permissions,
        expires_at=expires_at.isoformat(),
    )


@router.post("/caregiver-profile-invitations/accept", response_model=CaregiverProfileLinkOutput)
def accept_caregiver_profile_invitation(
    payload: AcceptCaregiverProfileInvitationInput,
    account: AuthAccount = Depends(current_account),
    db: Session = Depends(get_db),
):
    digest = hashlib.sha256(payload.token.encode("utf-8")).hexdigest()
    invitation = db.query(CaregiverProfileInvitation).filter(
        CaregiverProfileInvitation.token_digest == digest,
        CaregiverProfileInvitation.accepted_at.is_(None),
        CaregiverProfileInvitation.revoked_at.is_(None),
    ).first()
    if invitation is None:
        raise HTTPException(status_code=400, detail="Invitation is invalid, expired, or already used")
    expires_at = invitation.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="Invitation is invalid, expired, or already used")
    profile = db.query(CaregiverProfile).filter(
        CaregiverProfile.id == invitation.caregiver_profile_id,
        CaregiverProfile.person_id == invitation.person_id,
    ).first()
    if profile is None:
        raise HTTPException(status_code=400, detail="Invitation no longer refers to a valid caregiver profile")
    existing_link = db.query(CaregiverProfileAccountLink).filter(
        CaregiverProfileAccountLink.caregiver_profile_id == profile.id,
        CaregiverProfileAccountLink.is_active.is_(True),
        CaregiverProfileAccountLink.revoked_at.is_(None),
    ).first()
    if existing_link is not None:
        raise HTTPException(status_code=409, detail="Caregiver profile already has an active account link")
    grant = db.query(AccountPersonAccess).filter(
        AccountPersonAccess.account_id == account.id,
        AccountPersonAccess.person_id == invitation.person_id,
    ).first()
    if grant is None:
        grant = AccountPersonAccess(
            account_id=account.id,
            person_id=invitation.person_id,
            role="caregiver",
            permissions=invitation.permissions,
            is_active=True,
            granted_by_account_id=invitation.invited_by_account_id,
        )
        db.add(grant)
    elif grant.is_active and grant.revoked_at is None:
        grant.permissions = sorted(set((grant.permissions or []) + invitation.permissions))
    else:
        grant.role = "caregiver"
        grant.permissions = invitation.permissions
        grant.is_active = True
        grant.revoked_at = None
        grant.granted_by_account_id = invitation.invited_by_account_id
    link = CaregiverProfileAccountLink(
        account_id=account.id,
        caregiver_profile_id=profile.id,
        person_id=invitation.person_id,
        linked_by_account_id=invitation.invited_by_account_id,
        is_active=True,
    )
    db.add(link)
    invitation.accepted_by_account_id = account.id
    invitation.accepted_at = datetime.now(timezone.utc)
    db.commit()
    return CaregiverProfileLinkOutput(
        person_id=invitation.person_id,
        caregiver_profile_id=profile.id,
        account_id=account.id,
        permissions=grant.permissions or [],
    )


@router.get("/my-caregiver-links", response_model=list[CaregiverLinkOutput])
def list_my_caregiver_links(
    person_id: Optional[str] = None,
    account: AuthAccount = Depends(current_account),
    db: Session = Depends(get_db),
):
    """List only caregiver identities explicitly linked to the authenticated account."""
    query = db.query(CaregiverAccountLink).filter(
        CaregiverAccountLink.account_id == account.id,
        CaregiverAccountLink.is_active.is_(True),
        CaregiverAccountLink.revoked_at.is_(None),
    )
    if person_id:
        query = query.filter(CaregiverAccountLink.person_id == person_id)
    links = query.order_by(CaregiverAccountLink.created_at.asc()).all()
    output = []
    for link in links:
        grant = db.query(AccountPersonAccess).filter(
            AccountPersonAccess.account_id == account.id,
            AccountPersonAccess.person_id == link.person_id,
            AccountPersonAccess.is_active.is_(True),
            AccountPersonAccess.revoked_at.is_(None),
        ).first()
        output.append(CaregiverLinkOutput(
            person_id=link.person_id,
            caregiver_id=link.caregiver_id,
            account_id=link.account_id,
            permissions=grant.permissions or [] if grant else [],
        ))
    return output
