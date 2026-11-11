"""Authenticated intake for care-record privacy rights requests.

Intake is not verification or fulfilment. No export or deletion is performed here.
"""
import uuid
from datetime import datetime, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.anonymous_session import AnonymousSession
from app.models.privacy_delivery import AuditLogEvent
from app.models.privacy_request import DataSubjectRequest

router = APIRouter(prefix="/privacy/requests", tags=["privacy requests"])

RequestType = Literal["access", "export", "correction", "deletion"]
AuthorityBasis = Literal[
    "self",
    "parent_or_guardian",
    "legal_representative",
    "authorized_caregiver",
    "other",
]


class PrivacyRequestCreate(BaseModel):
    request_type: RequestType
    authority_basis: AuthorityBasis
    authority_attested: bool
    note: Optional[str] = Field(default=None, max_length=1000)


def _scope(request: Request, db: Session) -> tuple[str, str]:
    person_id = getattr(request.state, "scoped_person_id", None)
    caregiver_id = getattr(request.state, "scoped_caregiver_id", None)
    if person_id and caregiver_id:
        return str(person_id), str(caregiver_id)

    authorization = request.headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(status_code=401, detail="A valid SolenOS session is required")
    import hashlib
    token_hash = hashlib.sha256(token.strip().encode("utf-8")).hexdigest()
    session = (
        db.query(AnonymousSession)
        .filter(
            AnonymousSession.token_hash == token_hash,
            AnonymousSession.revoked_at.is_(None),
            AnonymousSession.expires_at > datetime.now(timezone.utc),
        )
        .first()
    )
    if session is None:
        raise HTTPException(status_code=401, detail="SolenOS session is invalid or expired")
    return str(session.person_id), str(session.caregiver_id)


def _serialize(item: DataSubjectRequest) -> dict:
    return {
        "id": item.id,
        "request_type": item.request_type,
        "status": item.request_status,
        "requested_at": item.requested_at.isoformat() if item.requested_at else None,
        "resolved_at": item.resolved_at.isoformat() if item.resolved_at else None,
        "resolution_code": item.resolution_code,
    }


@router.post("", status_code=201)
def submit_privacy_request(
    payload: PrivacyRequestCreate,
    request: Request,
    db: Session = Depends(get_db),
):
    person_id, caregiver_id = _scope(request, db)
    if payload.authority_attested is not True:
        raise HTTPException(
            status_code=422,
            detail="You must confirm your authority to submit this request.",
        )

    now = datetime.now(timezone.utc)
    request_id = str(uuid.uuid4())
    row = DataSubjectRequest(
        id=str(uuid.uuid4()),
        requester_id=caregiver_id,
        actor_id=caregiver_id,
        subject_id=person_id,
        person_id=person_id,
        caregiver_id=caregiver_id,
        request_type=payload.request_type,
        request_status="received_pending_authority_review",
        requested_at=now,
        request_id=request_id,
        correlation_id=request_id,
        policy_version="privacy-request-intake-v1",
        record_metadata={
            "authority_basis_claimed": payload.authority_basis,
            "authority_verified": False,
            "request_note": payload.note,
            "fulfilment_started": False,
        },
    )
    db.add(row)
    db.add(AuditLogEvent(
        actor_id=caregiver_id,
        subject_id=person_id,
        person_id=person_id,
        caregiver_id=caregiver_id,
        action="privacy_request.submitted",
        target_type="data_subject_request",
        target_id=row.id,
        occurred_at=now,
        outcome="received_pending_review",
        request_id=request_id,
        correlation_id=request_id,
        policy_version="privacy-request-intake-v1",
        record_metadata={
            "request_type": payload.request_type,
            "authority_verified": False,
        },
    ))
    db.commit()
    db.refresh(row)
    return {
        **_serialize(row),
        "message": (
            "Request received. Identity and authority must be verified before review. "
            "This submission does not itself export or delete data."
        ),
        "authority_verified": False,
        "fulfilment_started": False,
    }


@router.get("")
def list_my_privacy_requests(request: Request, db: Session = Depends(get_db)):
    person_id, caregiver_id = _scope(request, db)
    rows = (
        db.query(DataSubjectRequest)
        .filter(
            DataSubjectRequest.person_id == person_id,
            DataSubjectRequest.caregiver_id == caregiver_id,
            DataSubjectRequest.actor_id == caregiver_id,
        )
        .order_by(DataSubjectRequest.requested_at.desc(), DataSubjectRequest.id.desc())
        .limit(50)
        .all()
    )
    return {"requests": [_serialize(row) for row in rows]}
