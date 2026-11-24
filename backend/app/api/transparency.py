"""Caregiver-facing transparency evidence endpoint."""
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.caregiver_access import CaregiverAccessGrant
from app.services.caregiver_permissions import caregiver_has_permission
from app.services.transparency_evidence import build_transparency_evidence

router = APIRouter(prefix="/transparency", tags=["transparency"])


@router.get("/evidence")
def get_transparency_evidence(request: Request, db: Session = Depends(get_db)):
    """Return a read-only, minimum-necessary evidence snapshot for this session.

    The person and caregiver scope comes exclusively from the validated bearer
    session middleware. Caller-supplied scope IDs are neither accepted nor used.
    """
    person_id = getattr(request.state, "scoped_person_id", None)
    caregiver_id = getattr(request.state, "scoped_caregiver_id", None)
    if not person_id or not caregiver_id:
        raise HTTPException(status_code=401, detail="A verified caregiver session is required.")

    grant = db.query(CaregiverAccessGrant).filter(
        CaregiverAccessGrant.person_id == str(person_id),
        CaregiverAccessGrant.caregiver_id == str(caregiver_id),
        CaregiverAccessGrant.status == "active",
        CaregiverAccessGrant.revoked_at.is_(None),
    ).first()
    if grant is None:
        raise HTTPException(status_code=403, detail="Active caregiver access is required.")

    can_view_lcr = caregiver_has_permission(grant, "view_lcr")
    can_view_documents = caregiver_has_permission(grant, "view_documents")
    if not (can_view_lcr or can_view_documents):
        raise HTTPException(status_code=403, detail="You do not have permission to view transparency information.")

    return build_transparency_evidence(
        db,
        person_id=str(person_id),
        caregiver_id=str(caregiver_id),
        grant=grant,
    )
