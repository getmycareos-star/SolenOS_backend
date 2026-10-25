from fastapi import FastAPI, APIRouter, Depends, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
import asyncio
import hashlib
import os
import json
import re
from datetime import datetime, timezone
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session
from app.api.v1 import router as api_v1_router
from app.api.uncertainty import router as uncertainty_router
from app.api.change import router as change_router
from app.api.caregiver_intelligence import router as caregiver_intelligence_router
from app.api.safety import router as safety_router
from app.api.causal import router as causal_router
from app.api.collaboration import router as collaboration_router
from app.api.document_extract import router as document_extract_router
from app.api.care_delta import router as care_delta_router
from app.core.config import get_settings
from app.core.database import Base, database_schema_differences, get_db, SessionLocal
from app.models.anonymous_session import AnonymousSession
from app.models.interaction_session import InteractionSession
from app.models.care import Person, Caregiver, Evidence, CareEvent
from app.api.anonymous_sessions import router as anonymous_sessions_router
from app.schemas.situation import InteractionPauseResponse, SituationInput, SituationResponse
from pydantic import BaseModel
from app.services.situation import process_situation_input
from app.services.qwen_gateway import extract_review_candidates

settings = get_settings()
app = FastAPI(title=settings.app_name, version="0.1.0")
cors_origins_env = os.getenv("CORS_ORIGINS", "https://solenosai.netlify.app")
cors_origins = [origin.strip().rstrip("/") for origin in cors_origins_env.split(",") if origin.strip()]
app.add_middleware(
    CORSMiddleware, allow_origins=cors_origins, allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept", "Origin", "X-Requested-With"],
)

app.include_router(anonymous_sessions_router, prefix="/api/v1")
app.include_router(api_v1_router, prefix="/api/v1")
app.include_router(uncertainty_router, prefix="/api/v1")
app.include_router(change_router, prefix="/api/v1")
app.include_router(caregiver_intelligence_router, prefix="/api/v1")
app.include_router(safety_router, prefix="/api/v1")
app.include_router(causal_router, prefix="/api/v1")
app.include_router(collaboration_router, prefix="/api/v1")
app.include_router(document_extract_router, prefix="/api")
app.include_router(care_delta_router, prefix="/api/v1")

PUBLIC_V1_ENDPOINTS = {
    ("/api/v1/assistant/status", "GET"),
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
    # Account registration/login and anonymous bootstrap are the only session
    # endpoints allowed without an existing bearer credential.
    if path in {
        "/api/v1/sessions/anonymous",
        "/api/v1/sessions/register",
        "/api/v1/sessions/login",
    } and method == "POST":
        return await call_next(request)
    if path == "/api/v1/share-intake" and method == "POST":
        try:
            content_length = int(request.headers.get("content-length", "0"))
        except ValueError:
            content_length = 0
        if content_length > 8 * 1024 * 1024:
            return JSONResponse(status_code=413, content={"detail": "Share payload exceeds the 8 MB request limit"})
        return await call_next(request)
    if path.startswith("/api/v1/share-intake/") and method == "GET":
        return await call_next(request)
    if (path, method) in PUBLIC_V1_ENDPOINTS:
        return await call_next(request)

    authorization = request.headers.get("authorization", "")
    scheme, _, raw_token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not raw_token.strip():
        return JSONResponse(status_code=401, content={"detail": "A valid SolenOS session is required"}, headers={"WWW-Authenticate": "Bearer"})

    token_hash = hashlib.sha256(raw_token.strip().encode("utf-8")).hexdigest()
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        session = db.query(AnonymousSession).filter(
            AnonymousSession.token_hash == token_hash,
            AnonymousSession.revoked_at.is_(None),
            AnonymousSession.expires_at > now,
        ).first()
        if session is None:
            return JSONResponse(status_code=401, content={"detail": "SolenOS session is invalid or expired"}, headers={"WWW-Authenticate": "Bearer"})
        scoped_person_id = session.person_id
        scoped_caregiver_id = session.caregiver_id
        request.state.anonymous_session_id = session.id
        request.state.scoped_person_id = scoped_person_id
        request.state.scoped_caregiver_id = scoped_caregiver_id

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
        if path.startswith("/api/v1/community") or path == "/api/v1/upload" or path.startswith("/api/v1/assistant/"):
            target_person_id = scoped_person_id
        if not target_person_id and (path.startswith("/api/v1/evidence/") or path.startswith("/api/v1/events/")):
            target_person_id = path.rsplit("/", 1)[-1]
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
            sent = False
            async def receive_body():
                nonlocal sent
                if sent:
                    return {"type": "http.request", "body": b"", "more_body": False}
                sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            request._receive = receive_body
            request._body = body
        if not target_person_id:
            candidate_ids = [str(value) for value in path_params.values() if value]
            owners = set()
            for mapper in Base.registry.mappers:
                model = mapper.class_
                if not hasattr(model, "id") or not hasattr(model, "person_id"):
                    continue
                for candidate_id in candidate_ids:
                    try:
                        owner = db.query(model.person_id).filter(model.id == candidate_id).scalar()
                    except Exception:
                        continue
                    if owner:
                        owners.add(str(owner))
            if len(owners) == 1:
                target_person_id = owners.pop()
            elif len(owners) > 1:
                return JSONResponse(status_code=403, content={"detail": "Ambiguous care-record scope"})
        body_payload = {}
        if body:
            try:
                parsed_body = json.loads(body.decode("utf-8"))
                if isinstance(parsed_body, dict):
                    body_payload = parsed_body
            except (UnicodeDecodeError, json.JSONDecodeError):
                pass
        claimed_caregiver_id = None if path.endswith("/situation") and method == "GET" else (
            body_payload.get("caregiver_id") or body_payload.get("created_by_caregiver_id") or
            body_payload.get("uploaded_by_caregiver_id") or request.query_params.get("caregiver_id") or
            request.query_params.get("created_by_caregiver_id")
        )
        if claimed_caregiver_id and str(claimed_caregiver_id) != str(scoped_caregiver_id):
            return JSONResponse(status_code=403, content={"detail": "Caregiver attribution does not match this session"})
        if not target_person_id:
            return JSONResponse(status_code=403, content={"detail": "Request must identify a care record in the session scope"})
        if str(target_person_id) != str(scoped_person_id):
            return JSONResponse(status_code=403, content={"detail": "This session is not authorised for that care record"})
    return await call_next(request)


