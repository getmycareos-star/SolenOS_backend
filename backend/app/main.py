from fastapi import FastAPI, APIRouter, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from app.api.v1 import router as api_v1_router
from app.api.uncertainty import router as uncertainty_router
from app.api.change import router as change_router
from app.api.caregiver_intelligence import router as caregiver_intelligence_router
from app.api.safety import router as safety_router
from app.api.causal import router as causal_router
from app.api.authentication import router as authentication_router, current_account
from app.api.capture import router as capture_router
from app.api.collaboration import community_router, conversation_router, team_router, professional_router, router as collaboration_router
from app.core.authz import assert_person_access, assert_account_is_caregiver
from app.models.auth_identity import AuthAccount
from app.models.care import Person as PersonModel, Caregiver as CaregiverModel
from app.core.config import get_settings
from app.core.database import get_db
from app.schemas.situation import SituationInput, SituationResponse
from app.services.situation import process_situation_input

settings = get_settings()
app = FastAPI(title=settings.app_name, version="0.1.0")

app.include_router(authentication_router, prefix="/api/v1")
app.include_router(capture_router, prefix="/api/v1")
app.include_router(collaboration_router, prefix="/api/v1")
app.include_router(community_router, prefix="/api/v1")
app.include_router(conversation_router, prefix="/api/v1")
app.include_router(team_router, prefix="/api/v1")
app.include_router(professional_router, prefix="/api/v1")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in settings.cors_origins.split(",") if origin.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_v1_router, prefix="/api/v1")
app.include_router(uncertainty_router, prefix="/api/v1")
app.include_router(change_router, prefix="/api/v1")
app.include_router(caregiver_intelligence_router, prefix="/api/v1")
app.include_router(safety_router, prefix="/api/v1")
app.include_router(causal_router, prefix="/api/v1")

situation_router = APIRouter()


@situation_router.post("/situation", response_model=SituationResponse)
def post_situation(payload: SituationInput, db: Session = Depends(get_db), account: AuthAccount = Depends(current_account)):
    assert_person_access(db, account.id, payload.person_id, "write")
    person = db.query(PersonModel).filter(PersonModel.id == payload.person_id).first()
    if person is None:
        raise HTTPException(status_code=400, detail="Person not found")
    caregiver = db.query(CaregiverModel).filter(CaregiverModel.id == payload.caregiver_id).first()
    if caregiver is None:
        raise HTTPException(status_code=400, detail="Caregiver not found")
    if caregiver.person_id != payload.person_id:
        raise HTTPException(status_code=403, detail="Caregiver does not belong to this care record")
    assert_account_is_caregiver(db, account.id, caregiver.id, payload.person_id)
    try:
        result = process_situation_input(db, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return result


app.include_router(situation_router, prefix="/api")
app.include_router(situation_router, prefix="/api/v1")



@app.get("/ready")
def readiness_check(db: Session = Depends(get_db)):
    """Report readiness only when the configured database can execute a query."""
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError:
        raise HTTPException(status_code=503, detail="Database unavailable")
    return {"status": "ready", "database": "connected"}

@app.get("/health")
def health_check(db: Session = Depends(get_db)):
    """Return healthy only when the database can answer a lightweight query."""
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError:
        raise HTTPException(status_code=503, detail="Database unavailable")
    return {"status": "healthy", "app": settings.app_name, "database": "ok"}
