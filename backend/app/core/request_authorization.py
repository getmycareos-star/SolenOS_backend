import json
import re
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse
from jose import JWTError
from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import Base, SessionLocal
from app.core.security import decode_access_token
from app.models.auth import Account, CareMembership


PROTECTED_PREFIXES = ("/api/v1/", "/api/situation")
PUBLIC_EXACT_PATHS = {"/health"}
UUID_PATTERN = re.compile(r"^[0-9a-fA-F-]{16,}$")
CAREGIVER_ID_FIELDS = {
    "caregiver_id",
    "caregiverId",
    "created_by_caregiver_id",
    "uploaded_by_caregiver_id",
}


def _walk_values(value: Any):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key, child
            yield from _walk_values(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_values(child)


def _person_for_identifier(db, identifier: str) -> str | None:
    persons = Base.metadata.tables.get("persons")
    if persons is not None and db.execute(
        select(persons.c.id).where(persons.c.id == identifier).limit(1)
    ).first():
        return identifier

    # Resolve path IDs (event/evidence/insight/action/etc.) back to their care recipient.
    for table in Base.metadata.tables.values():
        if "id" not in table.c or "person_id" not in table.c:
            continue
        try:
            row = db.execute(
                select(table.c.person_id).where(table.c.id == identifier).limit(1)
            ).first()
            if row and row[0]:
                return str(row[0])
        except Exception:
            # A malformed/unavailable table must not grant access; continue searching.
            continue
    return None


async def authorize_api_request(request: Request, call_next):
    path = request.url.path
    if request.method == "OPTIONS" or path in PUBLIC_EXACT_PATHS or path in {"/docs", "/openapi.json", "/redoc"}:
        return await call_next(request)
    if not any(path.startswith(prefix) for prefix in PROTECTED_PREFIXES):
        return await call_next(request)
    if path.startswith("/api/v1/auth/"):
        return await call_next(request)

    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return JSONResponse(status_code=401, content={"detail": "Sign in to access care data."})

    try:
        claims = decode_access_token(token.strip())
    except (JWTError, RuntimeError):
        return JSONResponse(status_code=401, content={"detail": "Session is invalid or expired."})

    account_id = claims.get("sub")
    if not isinstance(account_id, str) or not account_id:
        return JSONResponse(status_code=401, content={"detail": "Session is invalid."})

    db = SessionLocal()
    try:
        account = db.query(Account).filter(Account.id == account_id).first()
        if not account or not account.is_active or claims.get("ver") != account.token_version:
            return JSONResponse(status_code=401, content={"detail": "Session is no longer active."})

        try:
            body = await request.body()
            payload = json.loads(body) if body else {}
        except (json.JSONDecodeError, UnicodeDecodeError):
            payload = {}

        person_ids: set[str] = set()
        query_person_id = request.query_params.get("person_id")
        if query_person_id:
            person_ids.add(query_person_id)
        for key, value in _walk_values(payload):
            if key in {"person_id", "personId"} and isinstance(value, str) and value:
                person_ids.add(value)

        # Check every resource ID in the path, not only a client-supplied person_id.
        path_ids = [part for part in path.split("/") if UUID_PATTERN.match(part)]
        for identifier in path_ids:
            resolved = _person_for_identifier(db, identifier)
            if resolved:
                person_ids.add(resolved)

        if len(person_ids) != 1:
            return JSONResponse(
                status_code=403,
                content={"detail": "A single authorized care record must be identified for this request."},
            )
        person_id = next(iter(person_ids))
        membership = (
            db.query(CareMembership)
            .filter(
                CareMembership.account_id == account.id,
                CareMembership.person_id == person_id,
                CareMembership.status == "active",
            )
            .first()
        )
        if not membership:
            return JSONResponse(status_code=403, content={"detail": "You are not a member of this care circle."})

        try:
            permissions = set(json.loads(membership.permissions))
        except (TypeError, json.JSONDecodeError):
            return JSONResponse(status_code=403, content={"detail": "Care-circle permissions are invalid."})
        required = "read" if request.method in {"GET", "HEAD"} else "write"
        if required not in permissions:
            return JSONResponse(status_code=403, content={"detail": "Your care-circle membership does not permit this action."})

        for key, value in _walk_values(payload):
            if key in CAREGIVER_ID_FIELDS and value is not None and value != membership.caregiver_id:
                return JSONResponse(status_code=403, content={"detail": "Caregiver attribution must match the authenticated care-circle membership."})

        request.state.account_id = account.id
        request.state.person_id = person_id
        request.state.caregiver_id = membership.caregiver_id
        request.state.care_permissions = permissions
    finally:
        db.close()

    return await call_next(request)
