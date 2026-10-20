import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
import re
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Header, status
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.responses import JSONResponse
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import (
    AuthenticatedPrincipal,
    get_current_principal,
    require_authenticated,
    require_care_context_access,
    get_current_user,
)
from app.models.identity import Person
from app.models.caregiver import Caregiver
from app.models.care_event import CaptureIdempotency, CareEvent
from app.services.situation import process_situation_input
from app.schemas.situation import SituationInput, SituationResponse

logger = logging.getLogger(__name__)


def _bearer_token(request: Request) -> Optional[str]:
    header = request.headers.get("authorization", "")
    scheme, separator, credentials = header.partition(" ")
    if separator and scheme.lower() == "bearer" and credentials.strip():
        return credentials.strip()
    return None


def _is_guest_care_key(value: Optional[str]) -> bool:
    return bool(
        value
        and value != "default_caregiver"
        and re.fullmatch(r"care_[A-Za-z0-9_-]{16,200}", value)
    )


def _ensure_guest_context(db: Session, care_key: str):
    """Create the minimum empty care context for a new browser-held capability key."""
    caregiver = db.query(Caregiver).filter(Caregiver.id == care_key).first()
    if caregiver is not None:
        person = db.query(Person).filter(Person.id == caregiver.person_id).first()
        if person is None or not person.is_active or person.deleted_at is not None:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Care context is unavailable")
        if not caregiver.is_active:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Care context is inactive")
        return caregiver, person

    if not _is_guest_care_key(care_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="A valid durable care key is required",
        )

    person = Person(name="Care recipient")
    db.add(person)
    db.flush()
    caregiver = Caregiver(
        id=care_key,
        name="Caregiver",
        email=f"{care_key}@guest.solenos.invalid",
        relationship="unspecified",
        person_id=person.id,
        is_active=True,
    )
    db.add(caregiver)
    try:
        db.commit()
    except IntegrityError:
        # Two first-load requests can race to initialize the same browser key.
        db.rollback()
        caregiver = db.query(Caregiver).filter(Caregiver.id == care_key).first()
        if caregiver is None:
            raise
        person = db.query(Person).filter(Person.id == caregiver.person_id).first()
        if person is None:
            raise HTTPException(status_code=500, detail="Care context initialization failed")
        return caregiver, person

    db.refresh(caregiver)
    db.refresh(person)
    return caregiver, person


def _principal_for_non_guest_request(
    request: Request,
    db: Session,
    token: Optional[str],
) -> AuthenticatedPrincipal:
    credentials = (
        HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
        if token
        else None
    )
    return get_current_user(request, db, credentials)


async def authorize_situation_context(
    request: Request,
    db: Session = Depends(get_db),
) -> None:
    """Authorize the no-onboarding caregiver path or an authenticated account.

    A random, browser-held `care_...` key is a bearer capability for this MVP
    route. It is never inferred from the public `default_caregiver` fallback.
    Account sessions remain supported as a separate path.
    """
    token = _bearer_token(request)
    query_caregiver_id = request.query_params.get("caregiver_id")

    existing_guest_caregiver = (
        db.query(Caregiver).filter(Caregiver.id == token).first()
        if token
        else None
    )
    if token and (_is_guest_care_key(token) or existing_guest_caregiver is not None):
        if query_caregiver_id and query_caregiver_id != token:
            raise HTTPException(status_code=403, detail="Care key does not match request context")
        if request.method == "GET":
            caregiver, person = _ensure_guest_context(db, token)
            request.state.situation_caregiver_id = caregiver.id
            request.state.situation_person_id = person.id
            request.state.situation_guest = True
            return

        try:
            payload = await request.json()
        except Exception:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        person_id = payload.get("person_id")
        caregiver_id = payload.get("caregiver_id")
        if not person_id or not caregiver_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Person and caregiver context are required",
            )
        if str(caregiver_id) != token:
            raise HTTPException(status_code=403, detail="Care key does not match caregiver attribution")
        caregiver = db.query(Caregiver).filter(
            Caregiver.id == token,
            Caregiver.person_id == str(person_id),
            Caregiver.is_active.is_(True),
        ).first()
        if caregiver is None:
            raise HTTPException(status_code=403, detail="Care key is not authorized for this person")
        person = db.query(Person).filter(
            Person.id == str(person_id),
            Person.is_active.is_(True),
            Person.deleted_at.is_(None),
        ).first()
        if person is None:
            raise HTTPException(status_code=404, detail="Person not found")
        request.state.situation_caregiver_id = caregiver.id
        request.state.situation_person_id = person.id
        request.state.situation_guest = True
        return

    # Account-authenticated access is supported for existing registered users.
    principal = _principal_for_non_guest_request(request, db, token)
    if request.method == "GET":
        caregiver_id = query_caregiver_id
        if not caregiver_id:
            raise HTTPException(status_code=400, detail="caregiver_id is required")
        caregiver = db.query(Caregiver).filter(
            Caregiver.id == caregiver_id,
            Caregiver.is_active.is_(True),
        ).first()
        if caregiver is None:
            raise HTTPException(status_code=404, detail="Caregiver not found")
        person_id = caregiver.person_id
    else:
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        person_id = payload.get("person_id")
        caregiver_id = payload.get("caregiver_id")
        if not person_id or not caregiver_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Person and caregiver context are required",
            )
        caregiver = db.query(Caregiver).filter(
            Caregiver.id == str(caregiver_id),
            Caregiver.person_id == str(person_id),
            Caregiver.is_active.is_(True),
        ).first()
        if caregiver is None:
            raise HTTPException(status_code=403, detail="Caregiver is not authorized for this person")

    person = db.query(Person).filter(
        Person.id == str(person_id),
        Person.is_active.is_(True),
        Person.deleted_at.is_(None),
    ).first()
    if person is None:
        raise HTTPException(status_code=404, detail="Person not found")
    require_authenticated(principal)
    require_care_context_access(principal, str(person_id), "situation capture")
    if str(principal.account.email or "").strip().lower() != str(caregiver.email or "").strip().lower():
        raise HTTPException(
            status_code=403,
            detail="Caregiver profile is not linked to the authenticated account and person",
        )
    request.state.situation_caregiver_id = caregiver.id
    request.state.situation_person_id = person.id
    request.state.situation_guest = False

router = APIRouter(
    prefix="/api",
    tags=["situation"],
    dependencies=[Depends(authorize_situation_context)],
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _request_fingerprint(payload: SituationInput) -> str:
    canonical = json.dumps(
        payload.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _cached_response(record: CaptureIdempotency, fingerprint: str):
    if record.request_hash != fingerprint:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Idempotency-Key was already used for a different capture request",
        )
    if record.response_status is None or record.response_json is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A request with this Idempotency-Key is already being processed",
        )
    if record.response_status == status.HTTP_200_OK:
        return SituationResponse.model_validate_json(record.response_json)
    return JSONResponse(
        status_code=record.response_status,
        content=json.loads(record.response_json),
    )


def _error_body(message: str, status_code: int) -> dict:
    # Match the application's stable HTTP error response shape so a retry
    # receives the same outcome as the first request.
    return {
        "error": message,
        "error_code": f"HTTP_{status_code}",
        "details": {"report_id": "unknown"},
    }


def _find_idempotency_record(
    db: Session,
    person_id: str,
    caregiver_id: str,
    idempotency_key: str,
) -> Optional[CaptureIdempotency]:
    return (
        db.query(CaptureIdempotency)
        .filter(
            CaptureIdempotency.person_id == person_id,
            CaptureIdempotency.caregiver_id == caregiver_id,
            CaptureIdempotency.idempotency_key == idempotency_key,
        )
        .first()
    )





@router.get("/situation")
def get_situation_context(
    request: Request,
    db: Session = Depends(get_db),
    offer_return_invite: bool = False,
):
    """Hydrate the durable Living Care Record for a browser-held care key."""
    caregiver_id = getattr(request.state, "situation_caregiver_id", None)
    person_id = getattr(request.state, "situation_person_id", None)
    if not caregiver_id or not person_id:
        raise HTTPException(status_code=500, detail="Authorized care context was not resolved")

    caregiver = db.query(Caregiver).filter(Caregiver.id == caregiver_id).first()
    person = db.query(Person).filter(Person.id == person_id).first()
    if caregiver is None or person is None:
        raise HTTPException(status_code=404, detail="Care context not found")

    care_events = (
        db.query(CareEvent)
        .filter(CareEvent.person_id == person_id)
        .order_by(CareEvent.created_at.asc(), CareEvent.id.asc())
        .all()
    )
    event_rows = []
    observations = []
    for event in care_events:
        raw_text = (event.description or event.title or "").strip()
        event_time = getattr(event, "event_time", None)
        created_at = getattr(event, "created_at", None)
        event_rows.append({
            "id": event.id,
            "raw_input": raw_text,
            "situation_id": getattr(event, "situation_id", None),
            "event_type": getattr(event.event_type, "value", event.event_type),
            "title": event.title,
            "description": event.description,
            "occurred_at": event_time.isoformat() if event_time else None,
            "created_at": created_at.isoformat() if created_at else None,
            "evidence_ids": event.evidence_ids or [],
            "attributes": {"source_situation_text": raw_text},
        })
        observations.append({
            "id": event.id,
            "raw_text": raw_text,
            "human_fact": None,
            "event_time": event_time.isoformat() if event_time else None,
            "evidence_ids": event.evidence_ids or [],
        })

    latest = care_events[-1] if care_events else None
    active_care_situation = (
        {
            "id": (getattr(latest, "situation_id", None) or person_id),
            "status": "active",
            "observations": observations,
        }
        if care_events
        else None
    )
    active_turn = (
        {
            "id": latest.id,
            "event_id": latest.id,
            "turn_number": len(care_events),
            "created_at": latest.created_at.isoformat() if latest.created_at else None,
        }
        if latest
        else None
    )

    display_name = (person.preferred_name or person.name or "").strip()
    return {
        "has_context_root": bool(care_events),
        "care_key": caregiver.id,
        "care_recipient_id": person.id,
        "care_recipient_display_name": display_name or None,
        "context": {
            "caregiver_id": caregiver.id,
            "care_recipient_id": person.id,
            "events": event_rows,
            "total_events": len(event_rows),
        },
        "active_care_situation": active_care_situation,
        "active_care_situation_turn": active_turn,
        "situations": [],
        "ui_situations": [],
        "care_situation_groups": [],
        "total_events": len(event_rows),
        "return_continuity": {
            "soft_invite": {"offered_now": False, "text": None},
            "is_long_absence": False,
            "recent_relevant_changes": [],
            "important_unresolved": [],
            "suppress_first_time_ux": bool(care_events),
        },
        "offer_return_invite_requested": offer_return_invite,
    }


@router.post("/situation", response_model=SituationResponse)
def create_situation(
    situation_input: SituationInput,
    db: Session = Depends(get_db),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
):
    """Record a care observation and replay the original result on retries.

    Clients should generate one Idempotency-Key per logical capture and reuse
    it only when retrying that same capture. Requests without a key retain the
    legacy at-least-once behavior and are not deduplicated.
    """
    key = (idempotency_key or "").strip()
    if not key:
        try:
            return process_situation_input(db, situation_input)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except HTTPException:
            raise
        except Exception as e:
            logger.exception("Situation processing failed")
            raise HTTPException(
                status_code=500,
                detail="Situation processing failed",
            ) from e

    if len(key) > 255:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Idempotency-Key must be 255 characters or fewer",
        )

    person_id = str(situation_input.person_id)
    caregiver_id = str(situation_input.caregiver_id)
    fingerprint = _request_fingerprint(situation_input)
    now = _utc_now()
    existing = _find_idempotency_record(db, person_id, caregiver_id, key)
    if existing is not None:
        expiry = existing.expires_at
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        if expiry <= now:
            db.delete(existing)
            db.commit()
            existing = None
        else:
            return _cached_response(existing, fingerprint)

    record = CaptureIdempotency(
        person_id=person_id,
        caregiver_id=caregiver_id,
        idempotency_key=key,
        request_hash=fingerprint,
        response_status=None,
        response_json=None,
        expires_at=now + timedelta(hours=24),
    )
    db.add(record)
    try:
        db.commit()
    except IntegrityError:
        # Another concurrent request may have reserved the same key between
        # our read and insert. Never process both copies.
        db.rollback()
        existing = _find_idempotency_record(db, person_id, caregiver_id, key)
        if existing is None:
            raise
        return _cached_response(existing, fingerprint)

    try:
        result = process_situation_input(db, situation_input)
        stored = _find_idempotency_record(db, person_id, caregiver_id, key)
        if stored is None:
            raise RuntimeError("Idempotency record disappeared during capture")
        stored.response_status = status.HTTP_200_OK
        stored.response_json = result.model_dump_json()
        db.commit()
        return result
    except ValueError as e:
        db.rollback()
        stored = _find_idempotency_record(db, person_id, caregiver_id, key)
        if stored is not None:
            stored.response_status = status.HTTP_400_BAD_REQUEST
            stored.response_json = json.dumps(
                _error_body(str(e), status.HTTP_400_BAD_REQUEST)
            )
            db.commit()
        raise HTTPException(status_code=400, detail=str(e)) from e
    except HTTPException as e:
        db.rollback()
        stored = _find_idempotency_record(db, person_id, caregiver_id, key)
        if stored is not None:
            stored.response_status = e.status_code
            stored.response_json = json.dumps(
                _error_body(str(e.detail), e.status_code)
            )
            db.commit()
        raise
    except Exception as e:
        logger.exception("Situation processing failed")
        db.rollback()
        stored = _find_idempotency_record(db, person_id, caregiver_id, key)
        if stored is not None:
            stored.response_status = status.HTTP_500_INTERNAL_SERVER_ERROR
            stored.response_json = json.dumps(
                _error_body(
                    "Situation processing failed",
                    status.HTTP_500_INTERNAL_SERVER_ERROR,
                )
            )
            db.commit()
        raise HTTPException(
            status_code=500,
            detail="Situation processing failed",
        ) from e
