from contextlib import asynccontextmanager
from fastapi import FastAPI, APIRouter, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from app.api.v1 import router as api_v1_router
from app.api.uncertainty import router as uncertainty_router
from app.api.change import router as change_router
from app.api.caregiver_intelligence import router as caregiver_intelligence_router
from app.api.safety import router as safety_router
from app.api.causal import router as causal_router
from app.api.meaning import router as meaning_router
from app.api.prioritization import router as prioritization_router
from app.api.knowledge_revision import router as knowledge_revision_router
from app.api.dementia import router as dementia_router
from app.api.llm import router as llm_router
from app.api.person import router as person_router
from app.api.person import behavior_router as behavior_router
from app.api.continuity import router as continuity_router
from app.api.what_matters import router as what_matters_router
from app.api.what_matters import trajectory_router as trajectory_router
from app.api.care_planning import router as care_planning_router
from app.api.care_planning import contradiction_router as contradiction_router
from app.api.engagement import router as follow_up_router
from app.api.engagement import proactive_router as proactive_router
from app.api.engagement import conversation_router as conversation_router
from app.api.daily_capture import router as daily_capture_router
from app.api.companion import router as companion_router
from app.api.daily_care import router as daily_care_router
from app.api.care_summary import router as summary_router
from app.api.actions import router as actions_router

try:
    from app.api.files import router as files_router
except ImportError:
    files_router = None
from app.core.config import get_settings
from app.core.database import get_db
from app.schemas.situation import SituationInput, SituationResponse
from app.services.situation import process_situation_input

settings = get_settings()
@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.core.database import init_db
    init_db()
    yield


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)


app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
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
app.include_router(meaning_router, prefix="/api/v1")
app.include_router(prioritization_router, prefix="/api/v1")
app.include_router(knowledge_revision_router, prefix="/api/v1")
app.include_router(dementia_router, prefix="/api/v1")
app.include_router(llm_router, prefix="/api/v1")
app.include_router(person_router, prefix="/api/v1")
app.include_router(behavior_router, prefix="/api/v1")
app.include_router(continuity_router, prefix="/api/v1")
app.include_router(what_matters_router, prefix="/api/v1")
app.include_router(trajectory_router, prefix="/api/v1")
app.include_router(care_planning_router, prefix="/api/v1")
app.include_router(contradiction_router, prefix="/api/v1")
app.include_router(follow_up_router, prefix="/api/v1")
app.include_router(proactive_router, prefix="/api/v1")
app.include_router(conversation_router, prefix="/api/v1")
app.include_router(daily_capture_router, prefix="/api/v1")
app.include_router(companion_router, prefix="/api/v1")
app.include_router(daily_care_router, prefix="/api/v1")
app.include_router(summary_router, prefix="/api/v1")
app.include_router(actions_router, prefix="/api/v1")
if files_router is not None:
    app.include_router(files_router, prefix="/api/files")


@app.get("/")
async def root():
    # The backend is API-only. The caregiver interface is the separate
    # frontend app, so this identifies the service and points at the real
    # interface instead of serving a placeholder page that would shadow it.
    return {
        "app": settings.app_name,
        "status": "running",
        "docs": "/docs",
        "api": "/api/v1",
        "role": "api",
        "interface": (
            "The caregiver interface is the separate frontend app "
            "(npm run dev, port 5173), which proxies /api here. Native builds "
            "are packaged from that same app via Capacitor."
        ),
    }

situation_router = APIRouter()


@situation_router.post("/situation", response_model=SituationResponse)
def post_situation(payload: SituationInput, db: Session = Depends(get_db)):
    try:
        result = process_situation_input(db, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return result


app.include_router(situation_router, prefix="/api")


@app.get("/health")
def health_check():
    return {"status": "healthy", "app": settings.app_name}
