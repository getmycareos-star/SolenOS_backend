import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Header, status
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
)
from app.models.identity import Person
from app.models.caregiver import Caregiver
from app.models.care_event import CaptureIdempotency
from app.services.situation import process_situation_input
from app.schemas.situation import SituationInput, SituationResponse

logger = logging.getLogger(__name__)


async def authorize_situation_context(
    request: Request,
    db: Session = Depends(get_db),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
) -> None:
    """Authenticate capture and bind both person and caregiver to the caller.

    IDs in a JSON body are references, not authorization. The person must be
    in the principal's care context, and the caregiver attribution must match
    an active caregiver profile linked by the authenticated account email.
    """
    require_authenticated(principal)
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

    person = db.query(Person).filter(Person.id == str(person_id)).first()
    if person is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Person not found")

    require_care_context_access(principal, str(person_id), "situation capture")

    caller_email = str(principal.account.email or "").strip().lower()
    caregiver = db.query(Caregiver).filter(
        Caregiver.id == str(caregiver_id),
        Caregiver.person_id == str(person_id),
        func.lower(Caregiver.email) == caller_email,
        Caregiver.is_active.is_(True),
    ).first()
    if caregiver is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Caregiver profile is not linked to the authenticated account and person",
        )


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
