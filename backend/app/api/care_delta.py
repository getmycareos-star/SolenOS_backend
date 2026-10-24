import json
import hashlib
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, Request, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.anonymous_session import AnonymousSession
from app.models.care_continuity import CareDeltaRun
from app.schemas.care_delta import CareDeltaReconcileRequest
from app.services.delta_engine import reconcile_event

router = APIRouter(prefix="/care-delta", tags=["care-delta"])


def _scoped_identity(request: Request, db: Session) -> tuple[str, str]:
    person_id = getattr(request.state, "scoped_person_id", None)
    caregiver_id = getattr(request.state, "scoped_caregiver_id", None)
    if person_id and caregiver_id:
        return str(person_id), str(caregiver_id)
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(status_code=401, detail="A valid SolenOS anonymous session is required")
    token_hash = hashlib.sha256(token.strip().encode("utf-8")).hexdigest()
    session = db.query(AnonymousSession).filter(
        AnonymousSession.token_hash == token_hash,
        AnonymousSession.revoked_at.is_(None),
        AnonymousSession.expires_at > datetime.now(timezone.utc),
    ).first()
    if session is None:
        raise HTTPException(status_code=401, detail="Anonymous session is invalid or expired")
    return str(session.person_id), str(session.caregiver_id)


@router.post("/reconcile")
def reconcile_care_event(payload: CareDeltaReconcileRequest, request: Request, db: Session = Depends(get_db)):
    scoped_person_id, scoped_caregiver_id = _scoped_identity(request, db)
    if str(payload.person_id) != str(scoped_person_id):
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="This session is not authorised for that care record")
    return reconcile_event(
        db, person_id=scoped_person_id, event_date=payload.event_date, source=payload.source,
        content=payload.content, source_event_id=payload.source_event_id,
        evidence_ids=payload.evidence_ids, persist=payload.persist,
        created_by_caregiver_id=scoped_caregiver_id,
    )



@router.get("/runs/{person_id}")
def list_delta_runs(person_id: str, request: Request, limit: int = 25, db: Session = Depends(get_db)):
    scoped_person_id, _ = _scoped_identity(request, db)
    if str(person_id) != str(scoped_person_id):
        raise HTTPException(status_code=403, detail="This session is not authorised for that care record")
    rows = db.query(CareDeltaRun).filter(
        CareDeltaRun.person_id == scoped_person_id
    ).order_by(CareDeltaRun.created_at.desc()).limit(max(1, min(limit, 100))).all()
    output = []
    for row in rows:
        try:
            delta = json.loads(row.delta_json)
        except (TypeError, json.JSONDecodeError):
            delta = None
        output.append({
            "id": row.id,
            "person_id": row.person_id,
            "source": row.source,
            "event_date": row.event_date,
            "source_event_id": row.source_event_id,
            "evidence_ids": row.evidence_ids or [],
            "model_name": row.model_name,
            "created_by_caregiver_id": row.created_by_caregiver_id,
            "created_at": row.created_at,
            "delta": delta,
        })
    return output
