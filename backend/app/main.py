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
from app.core.config import get_settings
from app.core.database import get_db
from app.schemas.situation import SituationInput, SituationResponse
from app.services.situation import process_situation_input

settings = get_settings()
app = FastAPI(title=settings.app_name, version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://localhost:3000",
        "https://solenosai.netlify.app",
    ],
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
def post_situation(payload: SituationInput, db: Session = Depends(get_db)):
    # A caller-supplied caregiver ID must belong to the same care recipient.
    # Daily-care capture already enforces this relationship; situation capture
    # must not allow a mismatched caregiver/person pair to create records.
    caregiver = (
        db.query(CaregiverModel)
        .filter(CaregiverModel.id == payload.caregiver_id)
        .first()
    )
    if caregiver and caregiver.person_id != payload.person_id:
        raise HTTPException(
            status_code=403,
            detail="Caregiver is not linked to this care record",
        )
    try:
        result = process_situation_input(db, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return result


app.include_router(situation_router, prefix="/api")


@app.get("/health")
def health_check():
    # Liveness only: confirms the ASGI process is responding.
    return {"status": "healthy", "app": settings.app_name}


@app.get("/ready")
def readiness_check(db: Session = Depends(get_db)):
    # Readiness verifies the dependency needed to persist care records.
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="Database is not ready") from exc
    return {"status": "ready", "database": "reachable", "app": settings.app_name}
