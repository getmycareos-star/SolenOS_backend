from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.care import Caregiver
from app.models.care_handoff import CareHandoff, CareHandoffEvent
from app.schemas.care_handoff import CareHandoffCreate, CareHandoffRead, CareHandoffEventRead

router = APIRouter(prefix="/care-handoffs", tags=["care-handoffs"])


def _scope(request: Request) -> tuple[str, str]:
    person_id = getattr(request.state, "scoped_person_id", None)
    caregiver_id = getattr(request.state, "scoped_caregiver_id", None)
    if not person_id or not caregiver_id:
        raise HTTPException(status_code=401, detail="An authenticated caregiver session is required")
    return str(person_id), str(caregiver_id)


def _caregiver_for_person(db: Session, caregiver_id: str, person_id: str) -> Caregiver:
    caregiver = db.query(Caregiver).filter(
        Caregiver.id == caregiver_id,
        Caregiver.person_id == person_id,
    ).first()
    if caregiver is None:
        raise HTTPException(status_code=422, detail="Caregiver must be linked to this care record")
    return caregiver


def _handoff(db: Session, handoff_id: str, person_id: str) -> CareHandoff:
    item = db.query(CareHandoff).filter(
        CareHandoff.id == handoff_id,
        CareHandoff.person_id == person_id,
    ).first()
    if item is None:
        raise HTTPException(status_code=404, detail="Care handoff not found")
    return item


def _event(db: Session, handoff: CareHandoff, actor_id: str, event_type: str, details: dict):
    db.add(CareHandoffEvent(
        handoff_id=handoff.id,
        person_id=handoff.person_id,
        actor_caregiver_id=actor_id,
        event_type=event_type,
        details=details,
        # Explicit UTC event time preserves chronological order on databases
        # whose server-side timestamp default has only second precision.
        created_at=datetime.now(timezone.utc),
    ))


@router.post("", response_model=CareHandoffRead, status_code=201)
def create_handoff(payload: CareHandoffCreate, request: Request, db: Session = Depends(get_db)):
    person_id, caregiver_id = _scope(request)
    if str(payload.person_id) != person_id:
        raise HTTPException(status_code=403, detail="This session is not authorised for that care record")
    _caregiver_for_person(db, caregiver_id, person_id)
    assigned_id = payload.assigned_to_caregiver_id
    if assigned_id:
        _caregiver_for_person(db, assigned_id, person_id)

    def clean_items(items):
        cleaned = [value.strip() for value in items if isinstance(value, str) and value.strip()]
        if len(cleaned) != len(items):
            raise HTTPException(status_code=422, detail="Handoff list items must be non-empty text")
        if any(len(value) > 2000 for value in cleaned):
            raise HTTPException(status_code=422, detail="Handoff list items must be 2000 characters or fewer")
        return cleaned

    item = CareHandoff(
        person_id=person_id,
        created_by_caregiver_id=caregiver_id,
        assigned_to_caregiver_id=assigned_id,
        title=payload.title.strip(),
        summary=payload.summary.strip(),
        outstanding_items=clean_items(payload.outstanding_items),
        recent_changes=clean_items(payload.recent_changes),
        safety_concerns=clean_items(payload.safety_concerns),
        status="open",
    )
    if not item.title or not item.summary:
        raise HTTPException(status_code=422, detail="Handoff title and summary must not be blank")
    db.add(item)
    db.flush()
    _event(db, item, caregiver_id, "created", {
        "title": item.title,
        "assigned_to_caregiver_id": assigned_id,
        "outstanding_count": len(item.outstanding_items),
        "recent_change_count": len(item.recent_changes),
        "safety_concern_count": len(item.safety_concerns),
    })
    db.commit()
    db.refresh(item)
    return item


@router.get("/people/{person_id}", response_model=list[CareHandoffRead])
def list_handoffs(person_id: str, request: Request, db: Session = Depends(get_db)):
    scoped_person_id, _ = _scope(request)
    if str(person_id) != scoped_person_id:
        raise HTTPException(status_code=403, detail="This session is not authorised for that care record")
    return db.query(CareHandoff).filter(
        CareHandoff.person_id == scoped_person_id,
    ).order_by(CareHandoff.created_at.desc(), CareHandoff.id.desc()).limit(200).all()


@router.get("/{handoff_id}", response_model=CareHandoffRead)
def get_handoff(handoff_id: str, request: Request, db: Session = Depends(get_db)):
    person_id, _ = _scope(request)
    return _handoff(db, handoff_id, person_id)


@router.get("/{handoff_id}/events", response_model=list[CareHandoffEventRead])
def list_handoff_events(handoff_id: str, request: Request, db: Session = Depends(get_db)):
    person_id, _ = _scope(request)
    item = _handoff(db, handoff_id, person_id)
    return db.query(CareHandoffEvent).filter(
        CareHandoffEvent.handoff_id == item.id,
        CareHandoffEvent.person_id == person_id,
    ).order_by(CareHandoffEvent.created_at.asc(), CareHandoffEvent.id.asc()).all()


@router.post("/{handoff_id}/acknowledge", response_model=CareHandoffRead)
def acknowledge_handoff(handoff_id: str, request: Request, db: Session = Depends(get_db)):
    person_id, caregiver_id = _scope(request)
    item = _handoff(db, handoff_id, person_id)
    _caregiver_for_person(db, caregiver_id, person_id)
    if item.status == "closed":
        raise HTTPException(status_code=409, detail="A closed handoff cannot be acknowledged")
    if item.assigned_to_caregiver_id and item.assigned_to_caregiver_id != caregiver_id:
        raise HTTPException(status_code=403, detail="Only the assigned caregiver can acknowledge this handoff")
    if item.status == "acknowledged":
        if item.acknowledged_by_caregiver_id == caregiver_id:
            return item
        raise HTTPException(status_code=409, detail="This handoff has already been acknowledged by another caregiver")
    item.status = "acknowledged"
    item.acknowledged_by_caregiver_id = caregiver_id
    item.acknowledged_at = datetime.now(timezone.utc)
    _event(db, item, caregiver_id, "acknowledged", {"acknowledged_at": item.acknowledged_at.isoformat()})
    db.commit()
    db.refresh(item)
    return item


@router.post("/{handoff_id}/close", response_model=CareHandoffRead)
def close_handoff(handoff_id: str, request: Request, db: Session = Depends(get_db)):
    person_id, caregiver_id = _scope(request)
    item = _handoff(db, handoff_id, person_id)
    _caregiver_for_person(db, caregiver_id, person_id)
    if item.assigned_to_caregiver_id and caregiver_id not in {
        item.assigned_to_caregiver_id, item.created_by_caregiver_id
    }:
        raise HTTPException(status_code=403, detail="Only the handoff creator or assigned caregiver can close this handoff")
    if item.status == "closed":
        if item.closed_by_caregiver_id == caregiver_id:
            return item
        raise HTTPException(status_code=409, detail="This handoff has already been closed by another caregiver")
    item.status = "closed"
    item.closed_by_caregiver_id = caregiver_id
    item.closed_at = datetime.now(timezone.utc)
    _event(db, item, caregiver_id, "closed", {"closed_at": item.closed_at.isoformat()})
    db.commit()
    db.refresh(item)
    return item
