import json
import re
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import hash_password, issue_access_token, verify_password
from app.models.auth import Account, CareMembership
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
    # A care record and its first caregiver membership are created atomically.
    person = Person(name=payload.name.strip())
    db.add(person)
    db.flush()
    caregiver = Caregiver(
        name=(account.display_name or account.email.split("@")[0]),
        email=account.email,
        relationship="family caregiver",
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
