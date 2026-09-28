from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional

from app.core.database import get_db
from app.schemas.care_context import (
    ContinuityHandoffCreate,
    ContinuityHandoffResponse,
    ContinuitySnapshotResponse,
    ContinuityThreadResponse,
)
from app.services.care_continuity import CareContinuityEngine

router = APIRouter(prefix="/continuity", tags=["continuity"])


def get_engine(db: Session = Depends(get_db)) -> CareContinuityEngine:
    return CareContinuityEngine(db)


@router.get("/{person_id}/brief")
def continuity_brief(
    person_id: str, engine: CareContinuityEngine = Depends(get_engine)
):
    return engine.continuity_brief(person_id)


@router.get("/{person_id}/threads", response_model=List[ContinuityThreadResponse])
def list_threads(
    person_id: str,
    include_stale: bool = True,
    engine: CareContinuityEngine = Depends(get_engine),
):
    return engine.list_threads(person_id, include_stale)


@router.post("/thread/touch", response_model=ContinuityThreadResponse)
def touch_thread(
    person_id: str,
    subject: str,
    label: Optional[str] = None,
    observed_at: Optional[str] = None,
    engine: CareContinuityEngine = Depends(get_engine),
):
    try:
        thread = engine.touch_thread(person_id, subject, label, observed_at)
        engine.db.commit()
        return thread
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/snapshot", response_model=ContinuitySnapshotResponse)
def build_snapshot(
    person_id: str,
    subject: Optional[str] = None,
    engine: CareContinuityEngine = Depends(get_engine),
):
    snapshot = engine.build_snapshot(person_id, subject)
    engine.db.commit()
    return snapshot


@router.post("/handoff", response_model=ContinuityHandoffResponse)
def build_handoff(
    payload: ContinuityHandoffCreate, engine: CareContinuityEngine = Depends(get_engine)
):
    handoff = engine.build_handoff(
        person_id=payload.person_id,
        reason=payload.reason,
        from_caregiver_id=payload.from_caregiver_id,
        to_caregiver_id=payload.to_caregiver_id,
        setting_change_note=payload.setting_change_note,
    )
    engine.db.commit()
    return handoff


@router.get("/{person_id}/handoffs", response_model=List[ContinuityHandoffResponse])
def list_handoffs(
    person_id: str, engine: CareContinuityEngine = Depends(get_engine)
):
    from app.models.continuity import ContinuityHandoff

    return (
        engine.db.query(ContinuityHandoff)
        .filter(ContinuityHandoff.person_id == person_id)
        .order_by(ContinuityHandoff.created_at.desc())
        .all()
    )
