from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.security import get_current_principal, require_care_context_access, require_authenticated, AuthenticatedPrincipal
from app.models.evidence import Evidence as EvidenceModel
from app.schemas.evidence import EvidenceResponse as EvidenceSchema

router = APIRouter()

# Include essential routers for security verification
from app.api.caregiver_intelligence import router as caregiver_intelligence_router
router.include_router(caregiver_intelligence_router)

from app.api.care_summary import router as care_summary_router
from app.api.care_summary import export_router as care_summary_export_router
router.include_router(care_summary_router)
router.include_router(care_summary_export_router)

@router.get('/evidence/{person_id}', response_model=list[EvidenceSchema])
def list_evidence(person_id: str, principal: AuthenticatedPrincipal = Depends(get_current_principal), db: Session = Depends(get_db)):
    require_authenticated(principal)
    # Authorization check: ensure the authenticated user can access the person whose evidence is being requested
    require_care_context_access(principal, person_id, 'evidence access')
    return db.query(EvidenceModel).filter(EvidenceModel.person_id == person_id).all()