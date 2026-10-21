from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.care import CareEvent, Caregiver, Person
from app.models.daily_care import DailyCareEntry
from app.schemas.daily_care import DailyCareEntryCreate, DailyCareEntryRead

router = APIRouter(tags=["daily-care"])


def _save_daily_care_entry(payload: DailyCareEntryCreate, db: Session) -> DailyCareEntry:
    person = db.query(Person).filter(Person.id == payload.person_id).first()
    if person is None:
        raise HTTPException(status_code=404, detail="Person not found")

    caregiver = (
        db.query(Caregiver)
        .filter(
            Caregiver.id == payload.caregiver_id,
            Caregiver.person_id == payload.person_id,
        )
        .first()
    )
    if caregiver is None:
        raise HTTPException(
            status_code=400,
            detail="Caregiver is not linked to the specified person",
        )

    entry = DailyCareEntry(**payload.model_dump())
    try:
        db.add(entry)
        db.flush()
        db.add(
            CareEvent(
                person_id=payload.person_id,
                event_type="daily_care_log",
                status="recorded",
                occurred_at=datetime.now(timezone.utc),
                title="Daily care log recorded",
                description="A structured daily care entry was recorded.",
                created_by_caregiver_id=payload.caregiver_id,
                location_provenance=payload.location_provenance,
                tags=["daily-care"],
            )
        )
        db.commit()
        db.refresh(entry)
        return entry
    except Exception:
        db.rollback()
        raise


@router.post("/daily-care/entries", response_model=DailyCareEntryRead)
@router.post("/daily/log", response_model=DailyCareEntryRead)
def create_daily_care_entry(
    payload: DailyCareEntryCreate,
    db: Session = Depends(get_db),
):
    """Persist a daily care entry submitted by either current capture screen."""
    return _save_daily_care_entry(payload, db)


@router.get("/daily-care/entries", response_model=list[DailyCareEntryRead])
def list_daily_care_entries(
    person_id: str,
    limit: int = 50,
    db: Session = Depends(get_db),
):
    if limit < 1 or limit > 100:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 100")
    person = db.query(Person).filter(Person.id == person_id).first()
    if person is None:
        raise HTTPException(status_code=404, detail="Person not found")
    return (
        db.query(DailyCareEntry)
        .filter(DailyCareEntry.person_id == person_id)
        .order_by(DailyCareEntry.created_at.desc())
        .limit(limit)
        .all()
    )
