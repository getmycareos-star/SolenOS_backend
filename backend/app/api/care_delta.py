from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.care_delta import CareDeltaReconcileRequest
from app.services.delta_engine import reconcile_event

router = APIRouter(prefix="/care-delta", tags=["care-delta"])


@router.post("/reconcile")
def reconcile_care_event(payload: CareDeltaReconcileRequest, request: Request, db: Session = Depends(get_db)):
    scoped_person_id = getattr(request.state, "scoped_person_id", None)
    scoped_caregiver_id = getattr(request.state, "scoped_caregiver_id", None)
    if not scoped_person_id or not scoped_caregiver_id:
        from fastapi import HTTPException
        raise HTTPException(status_code=401, detail="A valid SolenOS session is required")
    if str(payload.person_id) != str(scoped_person_id):
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="This session is not authorised for that care record")
    return reconcile_event(
        db, person_id=scoped_person_id, event_date=payload.event_date, source=payload.source,
        content=payload.content, source_event_id=payload.source_event_id,
        evidence_ids=payload.evidence_ids, persist=payload.persist,
        created_by_caregiver_id=scoped_caregiver_id,
    )
