from fastapi import FastAPI, APIRouter, Depends, HTTPException, Request
import asyncio
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
import hashlib
import json
import os
from datetime import datetime, timezone
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session
from app.api.v1 import router as api_v1_router
from app.api.uncertainty import router as uncertainty_router
from app.api.change import router as change_router
from app.api.caregiver_intelligence import router as caregiver_intelligence_router
from app.api.safety import router as safety_router
from app.api.care_continuity import router as care_continuity_router
from app.api.care_delta import router as care_delta_router
from app.api.care_circle import router as care_circle_router
from app.api.causal import router as causal_router
from app.core.config import get_settings
from app.core.database import Base, database_schema_differences, get_db, SessionLocal
from app.models.anonymous_session import AnonymousSession
from app.models.interaction_session import InteractionSession
from app.models.care import Person, Caregiver, Evidence, CareEvent
from app.api.anonymous_sessions import router as anonymous_sessions_router
from app.api.reminders import router as reminders_router
from app.api.medication_authority import router as medication_authority_router
from app.services.reminder_worker import reminder_scheduler_loop
from app.schemas.situation import InteractionPauseResponse, SituationInput, SituationResponse
from pydantic import BaseModel
from app.services.situation import process_situation_input

settings = get_settings()
app = FastAPI(title=settings.app_name, version="0.1.0")
_reminder_task = None


@app.on_event("startup")
async def start_reminder_scheduler():
    global _reminder_task
    enabled = os.getenv("ENABLE_REMINDER_SCHEDULER", "true").strip().lower() in {"1", "true", "yes", "on"}
    if enabled and (_reminder_task is None or _reminder_task.done()):
        _reminder_task = asyncio.create_task(reminder_scheduler_loop(), name="solenos-reminder-scheduler")


@app.on_event("shutdown")
async def stop_reminder_scheduler():
    global _reminder_task
    if _reminder_task is not None:
        _reminder_task.cancel()
        try:
            await _reminder_task
        except asyncio.CancelledError:
            pass
        _reminder_task = None

# Production CORS is explicitly controlled by CORS_ORIGINS. Keep local
# development origins only when no production allowlist is configured.
_cors_env = os.getenv("CORS_ORIGINS", "").strip()
if _cors_env:
    _allowed_origins = [origin.strip().rstrip("/") for origin in _cors_env.split(",") if origin.strip()]
elif settings.is_production:
    _allowed_origins = ["https://solenosai.netlify.app"]
else:
    _allowed_origins = [
        "http://localhost:5173",
        "http://localhost:3000",
        "https://solenosai.netlify.app",
    ]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(anonymous_sessions_router, prefix="/api/v1")
app.include_router(reminders_router, prefix="/api/v1")
app.include_router(medication_authority_router, prefix="/api/v1")
app.include_router(api_v1_router, prefix="/api/v1")
app.include_router(uncertainty_router, prefix="/api/v1")
app.include_router(change_router, prefix="/api/v1")
app.include_router(caregiver_intelligence_router, prefix="/api/v1")
app.include_router(safety_router, prefix="/api/v1")
app.include_router(causal_router, prefix="/api/v1")
app.include_router(care_continuity_router, prefix="/api/v1")
app.include_router(care_delta_router, prefix="/api/v1")
app.include_router(care_circle_router, prefix="/api/v1/care-circle")

# All care-data APIs require a server-issued bearer capability scoped to one
# person. The anonymous bootstrap is the only way to obtain a new capability;
# caller-supplied person/caregiver IDs are never accepted as credentials.
PUBLIC_V1_ENDPOINTS = {
    ("/api/v1/time/now", "GET"),
    ("/api/v1/time/ago", "POST"),
    ("/api/v1/time/until", "POST"),
}


@app.middleware("http")
async def enforce_scoped_anonymous_session(request: Request, call_next):
    path = str(request.scope.get("path") or request.url.path).rstrip("/")
    method = request.method.upper()
    if method == "OPTIONS":
        return await call_next(request)
    if not path.startswith("/api/v1/") and path != "/api/situation":
        return await call_next(request)
    if path == "/api/v1/sessions/anonymous" and method == "POST":
        return await call_next(request)
    if (path, method) in PUBLIC_V1_ENDPOINTS:
        return await call_next(request)

    authorization = request.headers.get("authorization", "")
    scheme, _, raw_token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not raw_token.strip():
        return JSONResponse(
            status_code=401,
            content={"detail": "A valid SolenOS anonymous session is required"},
            headers={"WWW-Authenticate": "Bearer"},
        )

    token_hash = hashlib.sha256(raw_token.strip().encode("utf-8")).hexdigest()
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        session = (
            db.query(AnonymousSession)
            .filter(
                AnonymousSession.token_hash == token_hash,
                AnonymousSession.revoked_at.is_(None),
                AnonymousSession.expires_at > now,
            )
            .first()
        )
        if session is None:
            return JSONResponse(
                status_code=401,
                content={"detail": "Anonymous session is invalid or expired"},
                headers={"WWW-Authenticate": "Bearer"},
            )
        scoped_person_id = session.person_id
        scoped_caregiver_id = session.caregiver_id
        request.state.anonymous_session_id = session.id
        request.state.scoped_person_id = scoped_person_id
        request.state.scoped_caregiver_id = scoped_caregiver_id
        if path.endswith("/sessions/refresh") and method == "POST":
            return await call_next(request)

        # Resolve FastAPI path parameters before executing the endpoint. Explicit
        # person_id parameters take precedence; record-specific IDs are resolved
        # against ORM models carrying a person_id column. Unknown/unscoped
        # operations fail closed rather than silently accepting an arbitrary ID.
        path_params = {}
        for route in request.app.routes:
            matcher = getattr(route, "matches", None)
            if matcher is None:
                continue
            try:
                match, child_scope = matcher(request.scope)
                if getattr(match, "value", None) == 2:
                    path_params = child_scope.get("path_params", {}) or {}
                    break
            except Exception:
                continue

        target_person_id = path_params.get("person_id") or request.query_params.get("person_id")
        if not target_person_id and (
            path.startswith("/api/v1/evidence/") or path.startswith("/api/v1/events/")
        ):
            # These legacy list endpoints use the care-recipient ID as the final
            # path segment. Resolve it explicitly before the session-scope check.
            target_person_id = path.rsplit("/", 1)[-1]
        if path.startswith("/api/v1/care-continuity/"):
            # Resolve care-record-scoped collection routes explicitly, rather
            # than trusting a client-provided person ID or a child-record ID.
            parts = path.split("/")
            if len(parts) >= 5 and parts[-2] in {
                "revisions", "assertions", "freshness", "next-questions", "outcomes", "contradictions"
            }:
                target_person_id = parts[-1]
        if path.endswith("/situation") and method == "GET":
            target_person_id = scoped_person_id
        query_person_id = request.query_params.get("person_id")
        if target_person_id and query_person_id and str(target_person_id) != str(query_person_id):
            return JSONResponse(status_code=403, content={"detail": "Conflicting care-record scope"})
        body = b""
        payload = {}
        if method in {"POST", "PUT", "PATCH", "DELETE"}:
            body = await request.body()
            try:
                payload = json.loads(body.decode("utf-8")) if body else {}
            except (UnicodeDecodeError, json.JSONDecodeError):
                payload = {}

            if isinstance(payload, dict):
                supplied_person_id = payload.get("person_id")
                if target_person_id and supplied_person_id and str(supplied_person_id) != str(target_person_id):
                    return JSONResponse(status_code=403, content={"detail": "Conflicting care-record scope"})

                # The legacy situation-capture UI has no server person ID yet.
                # Bind its write to the authenticated session rather than
                # trusting the client-generated caregiver key as identity.
                if path.endswith("/situation") and method in {"POST", "PATCH"}:
                    if supplied_person_id and str(supplied_person_id) != str(scoped_person_id):
                        return JSONResponse(status_code=403, content={"detail": "This session is not authorised for that care record"})
                    if method == "POST" and payload.get("caregiver_id"):
                        payload["client_care_key"] = payload["caregiver_id"]
                    payload["person_id"] = scoped_person_id
                    payload["caregiver_id"] = scoped_caregiver_id
                    target_person_id = scoped_person_id
                    body = json.dumps(payload).encode("utf-8")
                elif not target_person_id:
                    target_person_id = supplied_person_id

            # Re-feed the original or server-scoped JSON to FastAPI's body parser.
            sent = False
            async def receive_body():
                nonlocal sent
                if sent:
                    return {"type": "http.request", "body": b"", "more_body": False}
                sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            request._receive = receive_body
            # FastAPI may read the cached Request.body() after middleware has
            # replaced the payload; keep both body interfaces aligned.
            request._body = body

        if not target_person_id:
            # For routes addressing a child resource by its own ID, look up the
            # owner only through mapped tables with both id and person_id.
            candidate_ids = [str(value) for value in path_params.values() if value]
            owners = set()
            for mapper in Base.registry.mappers:
                model = mapper.class_
                if not hasattr(model, "id") or not hasattr(model, "person_id"):
                    continue
                for candidate_id in candidate_ids:
                    try:
                        owner = (
                            db.query(model.person_id)
                            .filter(model.id == candidate_id)
                            .scalar()
                        )
                    except Exception:
                        continue
                    if owner:
                        owners.add(str(owner))
            if len(owners) == 1:
                target_person_id = owners.pop()
            elif len(owners) > 1:
                return JSONResponse(status_code=403, content={"detail": "Ambiguous care-record scope"})

        # Caller-supplied caregiver IDs are attribution fields, not credentials.
        # If present, they must match the caregiver identity bound to this token.
        body_payload = {}
        if body:
            try:
                parsed_body = json.loads(body.decode("utf-8"))
                if isinstance(parsed_body, dict):
                    body_payload = parsed_body
            except (UnicodeDecodeError, json.JSONDecodeError):
                pass
        claimed_caregiver_id = None if path.endswith("/situation") and method == "GET" else (
            body_payload.get("caregiver_id")
            or body_payload.get("created_by_caregiver_id")
            or body_payload.get("updated_by_caregiver_id")
            or body_payload.get("resolved_by_caregiver_id")
            or body_payload.get("uploaded_by_caregiver_id")
            or request.query_params.get("caregiver_id")
            or request.query_params.get("created_by_caregiver_id")
        )
        if claimed_caregiver_id and str(claimed_caregiver_id) != str(scoped_caregiver_id):
            return JSONResponse(
                status_code=403,
                content={"detail": "Caregiver attribution does not match this session"},
            )

        # Care-continuity audit actors are always derived from the verified
        # bearer session. Reject spoofed IDs above; also fill omitted/null actor
        # fields so the persisted review history never silently loses authorship.
        if path.startswith("/api/v1/care-continuity/") and isinstance(body_payload, dict):
            actor_field = None
            if method == "POST" and path.endswith("/assertions"):
                actor_field = "created_by_caregiver_id"
            elif method == "POST" and path.endswith("/outcomes"):
                actor_field = "created_by_caregiver_id"
            elif method == "PATCH" and path.endswith("/status") and "/assertions/" in path:
                actor_field = "updated_by_caregiver_id"
            elif method == "PATCH" and path.endswith("/resolution") and "/contradictions/" in path:
                actor_field = "resolved_by_caregiver_id"
            if actor_field:
                body_payload[actor_field] = scoped_caregiver_id
                body = json.dumps(body_payload).encode("utf-8")
                request._body = body

        if not target_person_id:
            return JSONResponse(
                status_code=403,
                content={"detail": "Request must identify a care record in the session scope"},
            )
        if str(target_person_id) != str(scoped_person_id):
            return JSONResponse(
                status_code=403,
                content={"detail": "This session is not authorised for that care record"},
            )

    return await call_next(request)


situation_router = APIRouter()


@situation_router.get("/situation")
def get_situation(
    request: Request,
    caregiver_id: str = "",
    care_session_id: str = "",
    offer_return_invite: int = 0,
    db: Session = Depends(get_db),
):
    """Hydrate a care record from persisted evidence and events only.

    The bearer session determines the person scope. Legacy caregiver/session
    query values are UI continuity keys only and never authorize data access.
    This endpoint does not synthesize clinical interpretation or sample data.
    """
    person_id = getattr(request.state, "scoped_person_id", None)
    caregiver_scope_id = getattr(request.state, "scoped_caregiver_id", None)
    if not person_id or not caregiver_scope_id:
        raise HTTPException(status_code=401, detail="A valid anonymous session is required")

    person = db.query(Person).filter(Person.id == person_id).first()
    if not person:
        raise HTTPException(status_code=404, detail="Care recipient not found")
    caregiver = db.query(Caregiver).filter(
        Caregiver.id == caregiver_scope_id,
        Caregiver.person_id == person_id,
    ).first()
    if not caregiver:
        raise HTTPException(status_code=403, detail="Session caregiver is not linked to this care record")

    evidence_rows = (
        db.query(Evidence)
        .filter(Evidence.person_id == person_id)
        .order_by(Evidence.uploaded_at.asc(), Evidence.id.asc())
        .all()
    )
    events = (
        db.query(CareEvent)
        .filter(CareEvent.person_id == person_id)
        .order_by(CareEvent.occurred_at.asc(), CareEvent.id.asc())
        .all()
    )
    observations = []
    for evidence in evidence_rows:
        linked_event_ids = [
            event.id for event in events
            if evidence.id in (event.evidence_ids or [])
        ]
        observations.append({
            "id": evidence.id,
            "raw_text": evidence.source_text,
            "human_fact": evidence.source_text,
            "kind": "mixed",
            "captured_at": evidence.uploaded_at.isoformat() if evidence.uploaded_at else "",
            "event_ids": linked_event_ids,
            "epistemic_kind": "mixed",
            "contributor_id": evidence.uploaded_by_caregiver_id,
        })

    event_payload = [{
        "id": event.id,
        "event_type": event.event_type,
        "title": event.title,
        "description": event.description,
        "occurred_at": event.occurred_at.isoformat() if event.occurred_at else None,
        "evidence_ids": event.evidence_ids or [],
    } for event in events]

    active_situation = None
    active_turn = None
    if observations:
        first_timestamp = observations[0]["captured_at"]
        last_timestamp = observations[-1]["captured_at"]
        root_event_id = events[0].id if events else None
        active_situation = {
            "id": (events[0].situation_id if events and events[0].situation_id else None) or root_event_id or observations[0]["id"],
            "care_recipient_id": person_id,
            "caregiver_id": caregiver_scope_id,
            "opened_at": first_timestamp,
            "updated_at": last_timestamp,
            "root_event_id": root_event_id,
            "subject_label": person.name,
            "theme": "mixed",
            "observations": observations,
            "open_questions": [],
            "asked_questions": [],
            "understanding_stage": "gathering",
            "connection_note": None,
            "synthesis": None,
            "what_matters_now": None,
            "lifecycle_status": "active",
            "persistence_source": "relational_evidence_and_events_projection",
            "reasoning_state_persisted": False,
            "open_questions_available": False,
            "theme_classification_available": False,
        }
        # This is a projection of persisted evidence/events, not a persisted ACS/CRS
        # object. Make that limitation explicit and do not synthesize a clinical assessment.
        active_turn = {
            "situation": active_situation,
            # No supported synthesis exists in this hydration-only path; keep
            # the field explicit rather than inventing a conclusion.
            "what_seems_happening": None,
        }

    client_care_key = caregiver_id.strip() or care_session_id.strip() or caregiver_scope_id
    return {
        "ok": True,
        "has_context_root": bool(observations),
        "care_key": client_care_key,
        "care_recipient_id": person_id,
        "care_recipient_display_name": person.name,
        "total_events": len(events),
        "events": event_payload,
        "situations": [],
        "ui_situations": [],
        "context": {
            "caregiver_id": client_care_key,
            "care_recipient_id": person_id,
            "events": event_payload,
        },
        "active_care_situation": active_situation,
        "active_care_situation_turn": active_turn,
        "return_continuity": {
            "soft_invite": {"offered_now": False, "text": None},
            "is_long_absence": False,
            "recent_relevant_changes": [],
            "important_unresolved": [],
            "suppress_first_time_ux": bool(observations),
        },
    }


class SituationDisplayNameUpdate(BaseModel):
    person_id: str
    caregiver_id: str
    display_name: str


@situation_router.patch("/situation")
def update_situation_display_name(
    payload: SituationDisplayNameUpdate,
    db: Session = Depends(get_db),
):
    display_name = payload.display_name.strip()
    if not display_name:
        raise HTTPException(status_code=422, detail="display_name must not be blank")
    person = db.query(Person).filter(Person.id == payload.person_id).first()
    if not person:
        raise HTTPException(status_code=404, detail="Care recipient not found")
    caregiver = db.query(Caregiver).filter(
        Caregiver.id == payload.caregiver_id,
        Caregiver.person_id == payload.person_id,
    ).first()
    if not caregiver:
        raise HTTPException(status_code=403, detail="Session caregiver is not linked to this care record")
    person.name = display_name
    db.commit()
    db.refresh(person)
    return {
        "ok": True,
        "care_recipient_id": person.id,
        "care_recipient_display_name": person.name,
    }


@situation_router.post(
    "/situation",
    response_model=SituationResponse | InteractionPauseResponse,
)
def post_situation(
    payload: SituationInput,
    request: Request,
    db: Session = Depends(get_db),
):
    # Revalidate the bearer capability at the route boundary instead of relying
    # solely on middleware request.state propagation. This also protects this
    # write path if middleware behavior changes in a future framework upgrade.
    authorization = request.headers.get("authorization", "")
    scheme, _, raw_token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not raw_token.strip():
        raise HTTPException(status_code=401, detail="A valid anonymous session is required")
    token_hash = hashlib.sha256(raw_token.strip().encode("utf-8")).hexdigest()
    authorized_session = (
        db.query(AnonymousSession)
        .filter(
            AnonymousSession.token_hash == token_hash,
            AnonymousSession.revoked_at.is_(None),
            AnonymousSession.expires_at > datetime.now(timezone.utc),
        )
        .first()
    )
    if authorized_session is None:
        raise HTTPException(status_code=401, detail="A valid anonymous session is required")
    scoped_person_id = str(authorized_session.person_id)
    scoped_caregiver_id = str(authorized_session.caregiver_id)
    if str(payload.person_id) != scoped_person_id:
        raise HTTPException(status_code=403, detail="This session is not authorised for that care record")
    if str(payload.caregiver_id) != scoped_caregiver_id:
        raise HTTPException(status_code=403, detail="Caregiver attribution does not match this session")

    if payload.action == "pause_active_care_situation":
        interaction_session_id = (payload.care_session_id or "").strip()
        if not interaction_session_id:
            raise HTTPException(status_code=422, detail="care_session_id is required to pause an interaction")
        now = datetime.now(timezone.utc)
        row = (
            db.query(InteractionSession)
            .filter(
                InteractionSession.interaction_session_id == interaction_session_id,
                InteractionSession.person_id == scoped_person_id,
                InteractionSession.caregiver_id == scoped_caregiver_id,
            )
            .first()
        )
        if row is None:
            row = InteractionSession(
                interaction_session_id=interaction_session_id,
                person_id=scoped_person_id,
                caregiver_id=scoped_caregiver_id,
                paused_at=now,
            )
            db.add(row)
        elif row.paused_at is None:
            row.paused_at = now
        try:
            db.commit()
            db.refresh(row)
        except IntegrityError:
            db.rollback()
            row = (
                db.query(InteractionSession)
                .filter(
                    InteractionSession.interaction_session_id == interaction_session_id,
                    InteractionSession.person_id == scoped_person_id,
                    InteractionSession.caregiver_id == scoped_caregiver_id,
                )
                .first()
            )
            if row is None:
                raise HTTPException(status_code=503, detail="The interaction pause could not be persisted")
            if row.paused_at is None:
                row.paused_at = now
                try:
                    db.commit()
                    db.refresh(row)
                except Exception as exc:
                    db.rollback()
                    raise HTTPException(status_code=503, detail="The interaction pause could not be persisted") from exc
        except Exception as exc:
            db.rollback()
            raise HTTPException(status_code=503, detail="The interaction pause could not be persisted") from exc
        return {
            "ok": True,
            "action": "pause_active_care_situation",
            "interaction_session_id": row.interaction_session_id,
            "paused_at": row.paused_at,
            "care_reality_preserved": True,
        }

    try:
        result = process_situation_input(db, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # A new accepted care entry resumes only this interaction session, never the
    # underlying care situation lifecycle.
    interaction_session_id = (payload.care_session_id or "").strip()
    if interaction_session_id:
        row = (
            db.query(InteractionSession)
            .filter(
                InteractionSession.interaction_session_id == interaction_session_id,
                InteractionSession.person_id == scoped_person_id,
                InteractionSession.caregiver_id == scoped_caregiver_id,
            )
            .first()
        )
        if row is not None and row.paused_at is not None:
            row.paused_at = None
            db.commit()
    return result


app.include_router(situation_router, prefix="/api")


@app.get("/health")
def health_check():
    # Liveness only: confirms the ASGI process is responding.
    return {"status": "healthy", "app": settings.app_name}


@app.get("/ready")
def readiness_check(db: Session = Depends(get_db)):
    # A successful SELECT 1 proves connectivity only. Also verify that every
    # ORM-managed table is present before advertising the service as ready.
    try:
        db.execute(text("SELECT 1"))
        from sqlalchemy import inspect

        inspector = inspect(db.get_bind())
        existing_tables = set(inspector.get_table_names())
        required_tables = set(Base.metadata.tables.keys())
        missing_tables = required_tables - existing_tables
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="Database is not ready") from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Database schema could not be verified") from exc

    if missing_tables:
        raise HTTPException(status_code=503, detail="Database schema is incomplete")

    try:
        differences = database_schema_differences(db.get_bind())
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Database schema could not be verified") from exc
    if differences:
        raise HTTPException(status_code=503, detail="Database schema is incomplete")

    return {
        "status": "ready",
        "database": "reachable",
        "schema": "verified",
        "app": settings.app_name,
    }
