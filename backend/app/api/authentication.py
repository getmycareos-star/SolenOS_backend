"""Account authentication endpoints; account creation does not grant care-record access."""
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.models.auth_identity import AccountPersonAccess, AuthAccount
from app.models.care import Person
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
    grant = AccountPersonAccess(
        account_id=account.id,
        person_id=person.id,
        role="owner",
        permissions=["read", "write", "admin"],
        is_active=True,
        granted_by_account_id=account.id,
    )
    db.add(grant)
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
