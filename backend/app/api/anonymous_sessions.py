import hashlib
import secrets
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.anonymous_session import AnonymousSession
from app.models.care import Caregiver, Person

router = APIRouter(prefix="/sessions", tags=["anonymous sessions"])
SESSION_TTL_DAYS = 30


class AnonymousSessionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    caregiver_name: str = Field(default="Caregiver", min_length=1, max_length=120)
    date_of_birth: Optional[date] = None


@router.post("/anonymous", status_code=201)
def create_anonymous_session(payload: AnonymousSessionCreate, db: Session = Depends(get_db)):
    """Create a care recipient, anonymous caregiver, and scoped bearer capability.

    No account or email is required. The opaque token is returned once; only its
    SHA-256 digest is persisted. The token is bound to both the care recipient
    and the caregiver attribution used by legacy care-write schemas.
    """
    person_name = payload.name.strip()
    caregiver_name = payload.caregiver_name.strip() or "Caregiver"
    if not person_name:
        raise HTTPException(status_code=422, detail="name must not be blank")

    person = Person(name=person_name, date_of_birth=payload.date_of_birth)
    db.add(person)
    db.flush()

    caregiver = Caregiver(
        name=caregiver_name,
        email=None,
        relationship="Family",
        person_id=person.id,
    )
    db.add(caregiver)
    db.flush()

    raw_token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(days=SESSION_TTL_DAYS)
    session = AnonymousSession(
        person_id=person.id,
        caregiver_id=caregiver.id,
        token_hash=hashlib.sha256(raw_token.encode("utf-8")).hexdigest(),
        expires_at=expires_at,
    )
    db.add(session)
    db.commit()
    db.refresh(person)
    db.refresh(caregiver)

    return {
        "person": {
            "id": person.id,
            "name": person.name,
            "date_of_birth": person.date_of_birth.isoformat() if person.date_of_birth else None,
            "created_at": person.created_at.isoformat() if person.created_at else None,
        },
        "caregiver": {
            "id": caregiver.id,
            "name": caregiver.name,
            "relationship": caregiver.relationship,
        },
        "access_token": raw_token,
        "token_type": "Bearer",
        "expires_at": expires_at.isoformat(),
        "expires_in_seconds": SESSION_TTL_DAYS * 24 * 60 * 60,
    }
