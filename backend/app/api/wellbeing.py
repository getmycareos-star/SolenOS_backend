"""Caregiver wellbeing endpoints.

Every route is keyed by caregiver id and takes no person_id. That is not a
stylistic choice: the data here describes the caregiver, so making it reachable
by person id would make it readable by anyone holding a care link, which is
precisely the disclosure the storage model exists to prevent.
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.services.wellbeing import WellbeingService

router = APIRouter(prefix="/caregiver-wellbeing", tags=["caregiver-wellbeing"])


class CheckIn(BaseModel):
    caregiver_id: str
    axis: str
    score: int = Field(..., ge=1, le=5)
    note: Optional[str] = None


class CheckInResponse(BaseModel):
    id: str
    caregiver_id: str
    axis: str
    score: int
    direction: str
    low_label: Optional[str] = None
    high_label: Optional[str] = None
    note: Optional[str] = None
    checked_in_at: str


class DismissRequest(BaseModel):
    note: Optional[str] = None


class ActionRequest(BaseModel):
    caregiver_id: str


def _to_response(row) -> CheckInResponse:
    return CheckInResponse(
        id=row.id,
        caregiver_id=row.caregiver_id,
        axis=row.axis.value,
        score=row.score,
        direction=row.direction.value,
        low_label=row.low_label,
        high_label=row.high_label,
        note=row.note,
        checked_in_at=row.checked_in_at.isoformat(),
    )


def get_service(db: Session = Depends(get_db)) -> WellbeingService:
    return WellbeingService(db)


@router.post("/check-in", response_model=CheckInResponse)
def check_in(payload: CheckIn, service: WellbeingService = Depends(get_service)):
    """Record how the caregiver is doing.

    One axis, one moment, under a second. Anything heavier gets skipped, and a
    check-in nobody completes tells you nothing.
    """
    try:
        row = service.record_check_in(
            caregiver_id=payload.caregiver_id,
            axis=payload.axis,
            score=payload.score,
            note=payload.note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    service.db.commit()
    return _to_response(row)


@router.get("/{caregiver_id}")
def view(
    caregiver_id: str,
    days: int = 30,
    service: WellbeingService = Depends(get_service),
):
    """The caregiver's own view of themselves."""
    return service.view(caregiver_id, days=days)


@router.post("/refresh/{caregiver_id}")
def refresh(caregiver_id: str, service: WellbeingService = Depends(get_service)):
    """Re-read recent check-ins and record what the arithmetic shows."""
    observations = service.observe(caregiver_id)
    support = service.offer_support(caregiver_id)
    service.db.commit()
    return {
        "caregiver_id": caregiver_id,
        "new_observations": len(observations),
        "new_support_suggestions": len(support),
        "observations": [
            {"id": o.id, "signal": o.signal, "statement": o.statement} for o in observations
        ],
        "support": [{"id": s.id, "kind": s.kind, "description": s.description} for s in support],
    }


@router.post("/observation/{observation_id}/dismiss")
def dismiss(
    observation_id: str,
    payload: DismissRequest,
    service: WellbeingService = Depends(get_service),
):
    """Stop showing an observation.

    Permanent. The caregiver said it is not useful to them and the system does
    not get to decide otherwise.
    """
    try:
        observation = service.dismiss(observation_id, note=payload.note)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    service.db.commit()
    return {
        "id": observation.id,
        "is_dismissed": observation.is_dismissed,
        "dismissed_at": observation.dismissed_at.isoformat() if observation.dismissed_at else None,
    }


@router.post("/support/{action_id}/taken")
def support_taken(action_id: str, service: WellbeingService = Depends(get_service)):
    try:
        action = service.mark_support_taken(action_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    service.db.commit()
    return {"id": action.id, "is_taken": action.is_taken}


@router.get("/{caregiver_id}/support", response_model=List[dict])
def open_support(caregiver_id: str, service: WellbeingService = Depends(get_service)):
    return [
        {
            "id": a.id,
            "kind": a.kind,
            "description": a.description,
            "offered_at": a.offered_at.isoformat(),
        }
        for a in service.open_support(caregiver_id)
    ]
