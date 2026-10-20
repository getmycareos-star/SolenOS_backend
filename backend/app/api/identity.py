from fastapi import APIRouter, Depends, HTTPException, Body, Request
from sqlalchemy.orm import Session
from typing import Optional, Dict, Any, List
from datetime import datetime
from uuid import uuid4
from pydantic import BaseModel, EmailStr, Field

from app.core.database import get_db
from app.models.caregiver_intelligence import CaregiverProfile
from app.models.identity import Account
from app.services.authentication import (
    create_account,
    authenticate_account,
    verify_password,
    hash_password,
    create_anonymous_session,
)
from app.services.caregiver_intelligence import CaregiverIntelligenceService

router = APIRouter(prefix="/identity", tags=["identity"])


class IdentityLoginRequest(BaseModel):
    email: EmailStr
    password: str
    care_session_id: Optional[str] = None
    telemetry_user_id: Optional[str] = None


class IdentitySignupRequest(BaseModel):
    care_session_id: str
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    telemetry_user_id: Optional[str] = None


class IdentityResponse(BaseModel):
    user_id: str
    care_session_id: str
    care_graph: Optional[Dict[str, Any]] = None
    memory_nodes: Optional[List[Dict[str, Any]]] = None
    active_decisions: Optional[List[Dict[str, Any]]] = None
    identity_state: Dict[str, Any]


def _find_or_create_account(db: Session, email: str, password: Optional[str] = None) -> Account:
    """Find an Account by email, or create one.

    During signup, the password is hashed and stored. During login for a
    CaregiverProfile that predates Account creation, we create a stub Account.
    """
    account = db.query(Account).filter(
        Account.email == email.lower(),
        Account.is_active == True,
    ).first()

    if account:
        return account

    if password:
        return create_account(
            db=db,
            email=email,
            password=password,
            account_type="caregiver",
        )

    return None


@router.post("/login", response_model=IdentityResponse)
def identity_login(
    request: Request,
    login_data: IdentityLoginRequest,
    db: Session = Depends(get_db),
):
    """Restore care graph state for persistent user.

    Password is verified against the Account's password_hash — never accepted
    unconditionally. If no Account exists for this email, a 401 is returned.
    """
    # Find the account by email
    account = db.query(Account).filter(
        Account.email == login_data.email.lower(),
        Account.is_active == True,
    ).first()

    if not account or not account.password_hash:
        # Account may not exist yet if only CaregiverProfile was created.
        # In that case we cannot authenticate — require explicit signup.
        raise HTTPException(status_code=401, detail="Invalid credentials")

    # Verify password — proper Argon2id verification
    from app.services.authentication import InvalidCredentialsError
    if not verify_password(login_data.password, account.password_hash or ""):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    # Find or create caregiver profile
    profile = (
        db.query(CaregiverProfile)
        .filter(CaregiverProfile.email == login_data.email)
        .first()
    )
    if not profile:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    care_session_id = login_data.care_session_id or str(uuid4())

    return IdentityResponse(
        user_id=profile.id,
        care_session_id=care_session_id,
        care_graph={"nodes": [], "edges": []},
        memory_nodes=[],
        active_decisions=[],
        identity_state={
            "mode": "persistent",
            "auth_enabled": True,
            "has_stored_care_graph": False,
            "authenticated": True,
        },
    )


@router.post("/signup", response_model=IdentityResponse)
def identity_signup(
    request: Request,
    signup_data: IdentitySignupRequest,
    db: Session = Depends(get_db),
):
    """Bind ephemeral care state to persistent identity.

    Creates both an Account (with hashed password) and a CaregiverProfile.
    Password is hashed with Argon2id before storage — never stored in plaintext.
    """
    # Check if email exists on Account
    existing = db.query(Account).filter(
        Account.email == signup_data.email.lower(),
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail="Email already registered")

    # Create account with properly hashed password
    account = create_account(
        db=db,
        email=signup_data.email,
        password=signup_data.password,
        account_type="caregiver",
    )

    # Create caregiver profile
    service = CaregiverIntelligenceService(db)
    profile = service.create_caregiver_profile(
        type("ProfileCreate", (), {
            "person_id": signup_data.telemetry_user_id or str(uuid4()),
            "name": signup_data.email.split("@")[0],
            "email": signup_data.email,
            "relationship": "self",
        })()
    )

    return IdentityResponse(
        user_id=profile.id,
        care_session_id=signup_data.care_session_id,
        care_graph={"nodes": [], "edges": []},
        memory_nodes=[],
        active_decisions=[],
        identity_state={
            "mode": "persistent",
            "auth_enabled": True,
            "has_stored_care_graph": False,
            "authenticated": True,
        },
    )
