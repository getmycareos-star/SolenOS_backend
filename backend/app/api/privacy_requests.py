"""Authenticated intake and restricted human review for care-record privacy requests.

Review records an accountable decision only. It never exports, corrects, or deletes care data.
"""
import hashlib
import hmac
import os
import uuid
from datetime import datetime, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.anonymous_session import AnonymousSession
from app.models.privacy_delivery import AuditLogEvent
from app.models.privacy_request import DataSubjectRequest
from app.models.care import Correction, Insight

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
    correction_target_type: Optional[Literal["insight"]] = None
    correction_target_id: Optional[str] = Field(default=None, max_length=255)
    correction_text: Optional[str] = Field(default=None, max_length=10000)
    correction_reason: Optional[str] = Field(default=None, max_length=2000)


class PrivacyRequestReview(BaseModel):
    decision: Literal["approve", "reject"]
    review_basis: str = Field(min_length=10, max_length=1000)
    authority_evidence_checked: Optional[str] = Field(default=None, max_length=1000)


def _scope(request: Request, db: Session) -> tuple[str, str]:
    person_id = getattr(request.state, "scoped_person_id", None)
    caregiver_id = getattr(request.state, "scoped_caregiver_id", None)
    if person_id and caregiver_id:
        return str(person_id), str(caregiver_id)

    authorization = request.headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(status_code=401, detail="A valid SolenOS session is required")
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


def _privacy_reviewer(
    token: Optional[str] = Header(default=None, alias="X-SolenOS-Privacy-Reviewer-Token"),
) -> str:
    expected = os.getenv("SOLENOS_PRIVACY_REVIEWER_TOKEN", "").strip()
    reviewer_id = os.getenv("SOLENOS_PRIVACY_REVIEWER_ID", "").strip()
    if len(expected) < 32 or not reviewer_id or len(reviewer_id) > 120:
        raise HTTPException(status_code=503, detail="Privacy reviewer access is not configured")
    if not token or not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=401, detail="Invalid privacy reviewer credentials")
    return reviewer_id


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

    correction_fields = (
        payload.correction_target_type,
        payload.correction_target_id,
        payload.correction_text,
    )
    if payload.request_type == "correction" and any(correction_fields) and not all(
        value and str(value).strip() for value in correction_fields
    ):
        raise HTTPException(
            status_code=422,
            detail="A correction request must include target type, target ID and corrected text.",
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
            "correction": ({
                "target_type": payload.correction_target_type,
                "target_id": payload.correction_target_id,
                "corrected_text": payload.correction_text,
                "reason": payload.correction_reason,
            } if payload.request_type == "correction" and payload.correction_target_id else None),
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


@router.get("/reviewer/pending")
def list_pending_privacy_reviews(
    reviewer_id: str = Depends(_privacy_reviewer),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(DataSubjectRequest)
        .filter(DataSubjectRequest.request_status == "received_pending_authority_review")
        .order_by(DataSubjectRequest.requested_at.asc(), DataSubjectRequest.id.asc())
        .limit(100)
        .all()
    )
    # Return the minimum fields needed to identify and triage a request.
    return {
        "reviewer_id": reviewer_id,
        "requests": [
            {
                "id": row.id,
                "request_type": row.request_type,
                "requested_at": row.requested_at.isoformat() if row.requested_at else None,
                "person_id": row.person_id,
                "requester_id": row.requester_id,
                "authority_basis_claimed": (row.record_metadata or {}).get("authority_basis_claimed"),
            }
            for row in rows
        ],
    }


@router.post("/{request_id}/review")
def review_privacy_request(
    request_id: str,
    payload: PrivacyRequestReview,
    reviewer_id: str = Depends(_privacy_reviewer),
    db: Session = Depends(get_db),
):
    row = (
        db.query(DataSubjectRequest)
        .filter(DataSubjectRequest.id == request_id)
        .with_for_update()
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Privacy request not found")
    if row.request_status != "received_pending_authority_review":
        raise HTTPException(status_code=409, detail="Privacy request is no longer awaiting review")

    if payload.decision == "approve" and not (payload.authority_evidence_checked or "").strip():
        raise HTTPException(
            status_code=422,
            detail="An approval requires a description of the authority evidence checked.",
        )

    now = datetime.now(timezone.utc)
    outcome = "approved_pending_fulfilment_assessment" if payload.decision == "approve" else "rejected"
    row.request_status = outcome
    # Approval is not fulfilment: keep the request unresolved until a separate
    # export/correction/deletion workflow completes. Rejection is terminal here.
    row.resolved_at = now if payload.decision == "reject" else None
    row.resolution_code = "review_approved" if payload.decision == "approve" else "review_rejected"
    row.record_metadata = {
        **(row.record_metadata or {}),
        "review": {
            "reviewer_id": reviewer_id,
            "decision": payload.decision,
            "review_basis": payload.review_basis.strip(),
            "authority_evidence_checked": (payload.authority_evidence_checked or "").strip() or None,
            "reviewed_at": now.isoformat(),
            "authority_verified_by_reviewer": payload.decision == "approve",
            "fulfilment_started": False,
        },
    }
    db.add(AuditLogEvent(
        actor_id=reviewer_id,
        subject_id=row.subject_id,
        person_id=row.person_id,
        caregiver_id=row.caregiver_id,
        action="privacy_request.reviewed",
        target_type="data_subject_request",
        target_id=row.id,
        occurred_at=now,
        outcome=payload.decision,
        request_id=row.request_id,
        correlation_id=row.correlation_id,
        policy_version="privacy-request-review-v1",
        record_metadata={
            "request_type": row.request_type,
            "fulfilment_started": False,
        },
    ))
    try:
        db.commit()
        db.refresh(row)
    except Exception:
        db.rollback()
        raise
    return {
        **_serialize(row),
        "reviewer_id": reviewer_id,
        "authority_verified_by_reviewer": payload.decision == "approve",
        "fulfilment_started": False,
        "message": (
            "Review approved. A separate fulfilment assessment is still required; no data was exported, corrected, or deleted."
            if payload.decision == "approve"
            else "Review rejected. No data was exported, corrected, or deleted."
        ),
    }


@router.post("/{request_id}/fulfil-correction")
def fulfil_approved_correction(
    request_id: str,
    reviewer_id: str = Depends(_privacy_reviewer),
    db: Session = Depends(get_db),
):
    """Apply a reviewed insight correction while retaining its original value and provenance."""
    row = (
        db.query(DataSubjectRequest)
        .filter(DataSubjectRequest.id == request_id)
        .with_for_update()
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Privacy request not found")
    if row.request_type != "correction":
        raise HTTPException(status_code=409, detail="This privacy request is not a correction")
    metadata = row.record_metadata if isinstance(row.record_metadata, dict) else {}
    existing = metadata.get("fulfilment") if isinstance(metadata.get("fulfilment"), dict) else {}
    if row.request_status == "fulfilled" and existing.get("correction_id"):
        return {
            **_serialize(row),
            "fulfilment": existing,
            "idempotent_replay": True,
        }
    if row.request_status != "approved_pending_fulfilment_assessment":
        raise HTTPException(status_code=409, detail="Correction request has not been approved")
    correction_request = metadata.get("correction")
    if not isinstance(correction_request, dict):
        raise HTTPException(status_code=422, detail="Correction details were not supplied with the request")
    if correction_request.get("target_type") != "insight":
        raise HTTPException(status_code=422, detail="Only insight-description corrections are currently supported")
    target_id = str(correction_request.get("target_id") or "").strip()
    corrected_text = str(correction_request.get("corrected_text") or "").strip()
    if not target_id or not corrected_text:
        raise HTTPException(status_code=422, detail="Correction target and corrected text are required")

    insight = (
        db.query(Insight)
        .filter(Insight.id == target_id, Insight.person_id == row.person_id)
        .with_for_update()
        .first()
    )
    if insight is None:
        raise HTTPException(status_code=404, detail="Correction target was not found in this care record")
    original_text = insight.description
    if original_text == corrected_text:
        raise HTTPException(status_code=409, detail="The requested correction does not change the target")

    now = datetime.now(timezone.utc)
    correction = Correction(
        id=str(uuid.uuid4()),
        person_id=row.person_id,
        target_type="insight",
        target_id=insight.id,
        original_text=original_text,
        corrected_text=corrected_text,
        reason=str(correction_request.get("reason") or "Approved privacy correction request"),
        caregiver_id=row.caregiver_id,
    )
    db.add(correction)
    insight.description = corrected_text
    insight.updated_at = now
    row.request_status = "fulfilled"
    row.resolved_at = now
    row.resolution_code = "correction_fulfilled"
    fulfilment = {
        "operation": "correction",
        "correction_id": correction.id,
        "target_type": "insight",
        "target_id": insight.id,
        "fulfilled_at": now.isoformat(),
        "fulfilled_by": reviewer_id,
        "original_value_preserved_in_correction_record": True,
    }
    row.record_metadata = {
        **metadata,
        "fulfilment": fulfilment,
    }
    db.add(AuditLogEvent(
        actor_id=reviewer_id,
        subject_id=row.subject_id,
        person_id=row.person_id,
        caregiver_id=row.caregiver_id,
        action="privacy_request.correction_fulfilled",
        target_type="correction",
        target_id=correction.id,
        occurred_at=now,
        outcome="fulfilled",
        request_id=row.request_id,
        correlation_id=row.correlation_id,
        policy_version="privacy-correction-fulfilment-v1",
        record_metadata={
            "privacy_request_id": row.id,
            "target_type": "insight",
            "target_id": insight.id,
        },
    ))
    try:
        db.commit()
        db.refresh(row)
        db.refresh(correction)
    except Exception:
        db.rollback()
        raise
    return {
        **_serialize(row),
        "fulfilment": fulfilment,
        "correction": {
            "id": correction.id,
            "target_type": correction.target_type,
            "target_id": correction.target_id,
            "original_text": correction.original_text,
            "corrected_text": correction.corrected_text,
            "reason": correction.reason,
            "created_at": correction.created_at.isoformat() if correction.created_at else now.isoformat(),
        },
        "idempotent_replay": False,
    }
