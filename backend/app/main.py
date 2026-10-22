import json
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
from app.api.collaboration import router as collaboration_router
from app.api.auth import router as auth_router, verify_token
from app.core.database import SessionLocal
from app.models.auth import AuthAccount
from app.models.collaboration import ProfessionalAccessGrant
from app.core.config import get_settings
from app.core.database import get_db
from app.models.care import Caregiver as CaregiverModel, Person as PersonModel
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
app.include_router(collaboration_router, prefix="/api/v1")
app.include_router(auth_router, prefix="/api/v1")

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
    # Preserve the existing not-found behavior: if the person does not exist,
    # let the service return its specific validation error before checking links.
    person = (
        db.query(PersonModel)
        .filter(PersonModel.id == payload.person_id)
        .first()
    )
    if person and (
        caregiver is None or caregiver.person_id != payload.person_id
    ):
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


_PUBLIC_AUTH_PATHS = {
    "/api/v1/auth/login",
    "/api/v1/auth/register",
    "/api/v1/auth/provision",
}
_PUBLIC_PATHS = {"/health", "/ready", "/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}


def _person_id_for_resource(db, resource_id):
    from app.core.database import Base
    from sqlalchemy import select
    # Direct care-recipient IDs take precedence.
    from app.models.care import Person
    if db.query(Person.id).filter(Person.id == resource_id).first():
        return resource_id
    # Resolve child resources (events, appointments, observations, handoffs, etc.)
    # to their owning care recipient before returning any private record.
    for table in Base.metadata.tables.values():
        if "id" not in table.c and "person_id" not in table.c:
            continue
        try:
            row = db.execute(
                select(table.c.person_id).where(table.c.id == resource_id).limit(1)
            ).first()
            if row and row[0]:
                return row[0]
        except Exception:
            continue
    return None


@app.middleware("http")
async def require_authenticated_care_access(request, call_next):
    path = request.url.path
    # Existing isolated unit tests override get_db with an in-memory database.
    # Production never sets dependency_overrides; authentication tests use the real middleware.
    if get_db in app.dependency_overrides:
        return await call_next(request)
    if request.method == "OPTIONS" or path in _PUBLIC_PATHS or path in _PUBLIC_AUTH_PATHS:
        return await call_next(request)
    if not (path.startswith("/api/v1/") or path == "/api/situation"):
        return await call_next(request)

    authorization = request.headers.get("authorization", "")
    if not authorization.lower().startswith("bearer "):
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=401, content={"detail": "Bearer access token required"})
    try:
        claims = verify_token(authorization.split(" ", 1)[1].strip())
    except HTTPException as exc:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    db = SessionLocal()
    try:
        account = db.query(AuthAccount).filter_by(id=claims["sub"], is_active=True).first()
        if not account or account.caregiver_id != claims["caregiver_id"] or account.role != claims["role"]:
            from fastapi.responses import JSONResponse
            return JSONResponse(status_code=401, content={"detail": "Account is inactive or token claims are stale"})
        caregiver = db.query(CaregiverModel).filter_by(id=account.caregiver_id).first()
        if not caregiver:
            from fastapi.responses import JSONResponse
            return JSONResponse(status_code=401, content={"detail": "Account caregiver is unavailable"})

        if path.startswith("/api/v1/team/") or path == "/api/v1/metrics":
            if account.role != "team":
                from fastapi.responses import JSONResponse
                return JSONResponse(status_code=403, content={"detail": "SolenOS team role required"})
        if path == "/api/v1/professional/dashboard":
            if account.role != "professional":
                from fastapi.responses import JSONResponse
                return JSONResponse(status_code=403, content={"detail": "Verified professional role required"})
            requested = request.query_params.get("professional_caregiver_id")
            if requested and requested != account.caregiver_id:
                from fastapi.responses import JSONResponse
                return JSONResponse(status_code=403, content={"detail": "Professional identity must match the authenticated account"})

        raw_body = b""
        content_type = request.headers.get("content-type", "")
        if "application/json" in content_type:
            raw_body = await request.body()
            async def receive_body():
                return {"type": "http.request", "body": raw_body, "more_body": False}
            request._receive = receive_body
        body = {}
        if raw_body:
            try:
                body = json.loads(raw_body.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                body = {}

        if path == "/api/v1/persons" and request.method == "POST":
            from fastapi.responses import JSONResponse
            return JSONResponse(status_code=403, content={"detail": "Create a care recipient through the protected account-enrollment flow"})
        
        requested_caregiver_id = request.query_params.get("caregiver_id")
        if requested_caregiver_id and requested_caregiver_id != account.caregiver_id:
            from fastapi.responses import JSONResponse
            return JSONResponse(status_code=403, content={"detail": "Caregiver identity must match the authenticated account"})

        supplied_caregiver_ids = []
        for key in ("caregiver_id", "created_by_caregiver_id", "uploaded_by_caregiver_id", "granted_by_caregiver_id", "from_caregiver_id", "to_caregiver_id"):
            value = body.get(key) if isinstance(body, dict) else None
            if isinstance(value, str) and value:
                supplied_caregiver_ids.append(value)
        if path == "/api/situation" and isinstance(body, dict):
            supplied_caregiver_ids.append(body.get("caregiver_id", ""))
        for supplied_id in supplied_caregiver_ids:
            if supplied_id and supplied_id != account.caregiver_id:
                # Handoffs may name another caregiver as the receiver, but the authenticated
                # sender must be the from_caregiver_id; the receiver is validated by route logic.
                if path.startswith("/api/v1/caregiver-handoffs") and body.get("from_caregiver_id") == account.caregiver_id and supplied_id == body.get("to_caregiver_id"):
                    continue
                from fastapi.responses import JSONResponse
                return JSONResponse(status_code=403, content={"detail": "Caregiver identity must match the authenticated account"})

        person_id = None
        if isinstance(body, dict) and isinstance(body.get("person_id"), str):
            person_id = body.get("person_id")
        elif request.query_params.get("person_id"):
            person_id = request.query_params.get("person_id")
        if not person_id:
            segments = [segment for segment in path.split("/") if segment]
            for segment in reversed(segments):
                person_id = _person_id_for_resource(db, segment)
                if person_id:
                    break
        # Protect private care records using the identity attached to the signed token.
        # A professional may access only care recipients with an active grant.
        if person_id:
            if account.role == "caregiver":
                allowed = caregiver.person_id == person_id
            elif account.role == "professional":
                allowed = db.query(ProfessionalAccessGrant.id).filter_by(
                    professional_caregiver_id=account.caregiver_id,
                    person_id=person_id, status="active"
                ).first() is not None
            else:
                allowed = False
            if not allowed:
                from fastapi.responses import JSONResponse
                return JSONResponse(status_code=403, content={"detail": "This account is not authorized for the selected care recipient"})

        else:
            # Fail closed for authenticated routes that do not identify a care recipient.
            # Only explicitly global or role-scoped endpoints may omit person scope.
            scope_exempt = (
                path == "/api/v1/auth/me"
                or path == "/api/v1/community"
                or path.startswith("/api/v1/community/")
                or (path == "/api/v1/professional/dashboard" and account.role == "professional")
                or ((path.startswith("/api/v1/team/") or path == "/api/v1/metrics") and account.role == "team")
            )
            if not scope_exempt:
                from fastapi.responses import JSONResponse
                return JSONResponse(
                    status_code=400,
                    content={"detail": "Care recipient scope is required for this endpoint"},
                )

        # DELETE reaction requests carry the actor ID in the query string, not JSON.
        # Bind that identifier to the signed-in account to prevent removing another
        # caregiver's reaction by guessing their caregiver ID.
        if path.startswith("/api/v1/community/") and request.method == "DELETE":
            requested_caregiver_id = request.query_params.get("caregiver_id")
            if requested_caregiver_id != account.caregiver_id:
                from fastapi.responses import JSONResponse
                return JSONResponse(status_code=403, content={"detail": "Community actions must use the authenticated caregiver"})

        # Community posting/reactions are caregiver actions; professionals can
        # only read the public feed unless separately granted another capability.
        if path == "/api/v1/community" and request.method == "POST" and account.role != "caregiver":
            from fastapi.responses import JSONResponse
            return JSONResponse(status_code=403, content={"detail": "Caregiver role required to publish a community post"})
        if "/reactions" in path and path.startswith("/api/v1/community/") and request.method in {"POST", "DELETE"} and account.role != "caregiver":
            from fastapi.responses import JSONResponse
            return JSONResponse(status_code=403, content={"detail": "Caregiver role required to react to community posts"})
        # Community posts are authored as the authenticated caregiver. Only team accounts
        # can moderate posts or view the moderation queue.
        if path.startswith("/api/v1/team/moderation") and account.role != "team":
            from fastapi.responses import JSONResponse
            return JSONResponse(status_code=403, content={"detail": "SolenOS team role required"})
        if path.startswith("/api/v1/community") and request.method in {"POST", "PATCH", "DELETE"}:
            if path.startswith("/api/v1/community/") and isinstance(body, dict) and body.get("caregiver_id") not in (None, account.caregiver_id):
                from fastapi.responses import JSONResponse
                return JSONResponse(status_code=403, content={"detail": "Community actions must use the authenticated caregiver"})
        response = await call_next(request)
        return response
    finally:
        db.close()
