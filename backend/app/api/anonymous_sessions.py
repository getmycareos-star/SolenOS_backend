import hashlib
import secrets
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.care import Person
from app.models.anonymous_session import AnonymousSession

router = APIRouter(prefix="/sessions", tags=["anonymous sessions"])
SESSION_TTL_DAYS = 30


class AnonymousSessionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    date_of_birth: Optional[date] = None


@router.post("/anonymous", status_code=201)
def create_anonymous_session(payload: AnonymousSessionCreate, db: Session = Depends(get_db)):
    """Create a care record and its first server-issued, record-scoped capability.

    No account or email is required. The returned bearer token is shown once;
    only its digest is stored server-side.
    """
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="name must not be blank")

    person = Person(name=name, date_of_birth=payload.date_of_birth)
    raw_token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(days=SESSION_TTL_DAYS)
    session = AnonymousSession(
        person_id=person.id or "",
        token_hash=hashlib.sha256(raw_token.encode("utf-8")).hexdigest(),
        expires_at=expires_at,
    )
    # SQLAlchemy applies the Person ID default at flush; persist the person
    # first so the foreign-key relationship is explicit and reliable.
    db.add(person)
    db.flush()
    session.person_id = person.id
    db.add(session)
    db.commit()
    db.refresh(person)
    return {
        "person": {
            "id": person.id,
            "name": person.name,
            "date_of_birth": person.date_of_birth.isoformat() if person.date_of_birth else None,
            "created_at": person.created_at.isoformat() if person.created_at else None,
        },
        "access_token": raw_token,
        "token_type": "Bearer",
        "expires_at": expires_at.isoformat(),
        "expires_in_seconds": SESSION_TTL_DAYS * 24 * 60 * 60,
    }
