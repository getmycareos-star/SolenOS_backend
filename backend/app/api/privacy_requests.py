"""Authenticated intake and restricted human review for care-record privacy requests.

Review records an accountable decision only. It never exports, corrects, or deletes care data.
"""
import base64
import hashlib
import hmac
import io
import json
import os
import re
import uuid
import zipfile
from datetime import datetime, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import Base, get_db
from app.models.anonymous_session import AnonymousSession
from app.models.privacy_delivery import AuditLogEvent
from app.models.privacy_request import DataSubjectRequest
from app.models.care import Correction, Insight, Evidence
from app.models.collaboration import EvidenceUpload
from app.models.privacy_export import PrivacyExportArtifact

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




def _export_value(value):
    if isinstance(value, (datetime,)):
        return value.isoformat()
    if isinstance(value, bytes):
        return {"encoding": "base64", "value": base64.b64encode(value).decode("ascii")}
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            pass
    return value


def _build_caregiver_export(db: Session, person_id: str, caregiver_id: str) -> tuple[bytes, dict]:
    """Build a deterministic export of person records and caregiver-attributed data."""
    archive_buffer = io.BytesIO()
    manifest = {
        "format": "solenos-privacy-export-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": {"person_id": person_id, "requesting_caregiver_id": caregiver_id},
        "record_sets": {},
        "document_files": [],
        "exclusions": [
            "Rows explicitly attributed to a different caregiver are excluded.",
            "Provider backups, infrastructure logs and third-party processor data are not included.",
            "Derived rows without caregiver attribution are included only when person-scoped.",
        ],
    }
    owner_columns = (
        "caregiver_id",
        "created_by_caregiver_id",
        "uploaded_by_caregiver_id",
        "reported_by_caregiver_id",
        "collected_by_caregiver_id",
        "changed_by_caregiver_id",
        "updated_by_caregiver_id",
        "actor_id",
        "requester_id",
        "created_by",
    )
    with zipfile.ZipFile(archive_buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for table in Base.metadata.sorted_tables:
            name = table.name
            if name in {"privacy_export_artifacts", "community_posts", "community_reactions", "share_intakes"}:
                continue
            columns = set(table.c.keys())
            if name == "persons":
                predicate = table.c.id == person_id
            elif name == "caregivers":
                predicate = table.c.id == caregiver_id
            elif "person_id" in columns:
                predicate = table.c.person_id == person_id
                owner_column = next((column for column in owner_columns if column in columns), None)
                if owner_column:
                    predicate = predicate & (table.c[owner_column] == caregiver_id)
            elif "caregiver_id" in columns:
                predicate = table.c.caregiver_id == caregiver_id
            else:
                continue
            rows = db.execute(table.select().where(predicate)).mappings().all()
            if not rows:
                continue
            records = []
            for row in rows:
                records.append({key: _export_value(value) for key, value in row.items()})
            archive.writestr(
                f"records/{re.sub(r'[^a-zA-Z0-9_.-]', '_', name)}.json",
                json.dumps(records, ensure_ascii=False, sort_keys=True, default=str),
            )
            manifest["record_sets"][name] = len(records)

        # Original binary documents are exported only when uploaded by this caregiver.
        uploads = (
            db.query(EvidenceUpload, Evidence)
            .join(Evidence, Evidence.id == EvidenceUpload.evidence_id)
            .filter(
                Evidence.person_id == person_id,
                Evidence.uploaded_by_caregiver_id == caregiver_id,
                Evidence.type == "document",
            )
            .order_by(EvidenceUpload.created_at.asc(), EvidenceUpload.evidence_id.asc())
            .all()
        )
        for upload, evidence in uploads:
            raw_name = os.path.basename(upload.filename or f"{evidence.id}.bin")
            safe_name = re.sub(r"[^a-zA-Z0-9_.-]", "_", raw_name)[:180] or f"{evidence.id}.bin"
            path = f"documents/{evidence.id}_{safe_name}"
            content = bytes(upload.content)
            if len(content) != upload.size_bytes or hashlib.sha256(content).hexdigest() != upload.sha256:
                raise HTTPException(status_code=500, detail="A stored document failed its integrity check; export was not completed")
            archive.writestr(path, content)
            manifest["document_files"].append({
                "evidence_id": evidence.id,
                "filename": safe_name,
                "mime_type": upload.mime_type,
                "size_bytes": upload.size_bytes,
                "sha256": upload.sha256,
                "archive_path": path,
            })
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2, default=str))
    return archive_buffer.getvalue(), manifest


@router.post("/{request_id}/fulfil-export")
def fulfil_approved_export(
    request_id: str,
    reviewer_id: str = Depends(_privacy_reviewer),
    db: Session = Depends(get_db),
):
    """Generate and persist an expiring ZIP for an approved, authority-reviewed export."""
    row = (
        db.query(DataSubjectRequest)
        .filter(DataSubjectRequest.id == request_id)
        .with_for_update()
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Privacy request not found")
    if row.request_type != "export":
        raise HTTPException(status_code=409, detail="This privacy request is not an export")
    if row.request_status not in {"approved_pending_fulfilment_assessment", "fulfilled"}:
        raise HTTPException(status_code=409, detail="Export request has not been approved")
    now = datetime.now(timezone.utc)
    existing = db.query(PrivacyExportArtifact).filter(
        PrivacyExportArtifact.request_id == row.id,
        PrivacyExportArtifact.person_id == row.person_id,
        PrivacyExportArtifact.caregiver_id == row.caregiver_id,
    ).with_for_update().first()
    existing_expiry = existing.expires_at if existing is not None else None
    if existing_expiry is not None and existing_expiry.tzinfo is None:
        existing_expiry = existing_expiry.replace(tzinfo=timezone.utc)
    if existing is not None and existing_expiry > now:
        return {
            **_serialize(row),
            "fulfilment": {
                "operation": "export",
                "artifact_id": existing.id,
                "filename": existing.filename,
                "size_bytes": existing.size_bytes,
                "sha256": existing.sha256,
                "expires_at": existing.expires_at.isoformat(),
                "download_path": f"/api/v1/privacy/requests/{row.id}/export",
            },
            "idempotent_replay": True,
        }

    archive_bytes, manifest = _build_caregiver_export(db, str(row.person_id), str(row.caregiver_id))
    expires_at = now + __import__("datetime").timedelta(hours=24)
    filename = f"solenos-privacy-export-{row.id}.zip"
    digest = hashlib.sha256(archive_bytes).hexdigest()
    if existing is None:
        existing = PrivacyExportArtifact(
            id=str(uuid.uuid4()),
            request_id=row.id,
            person_id=str(row.person_id),
            caregiver_id=str(row.caregiver_id),
            filename=filename,
            content=archive_bytes,
            size_bytes=len(archive_bytes),
            sha256=digest,
            expires_at=expires_at,
        )
        db.add(existing)
    else:
        existing.content = archive_bytes
        existing.filename = filename
        existing.size_bytes = len(archive_bytes)
        existing.sha256 = digest
        existing.expires_at = expires_at
        existing.downloaded_at = None

    row.request_status = "fulfilled"
    row.resolved_at = now
    row.resolution_code = "export_fulfilled"
    fulfilment = {
        "operation": "export",
        "artifact_id": existing.id,
        "filename": filename,
        "size_bytes": len(archive_bytes),
        "sha256": digest,
        "expires_at": expires_at.isoformat(),
        "fulfilled_at": now.isoformat(),
        "fulfilled_by": reviewer_id,
        "record_sets": manifest["record_sets"],
        "document_count": len(manifest["document_files"]),
    }
    metadata = row.record_metadata if isinstance(row.record_metadata, dict) else {}
    row.record_metadata = {**metadata, "fulfilment": fulfilment}
    db.add(AuditLogEvent(
        actor_id=reviewer_id,
        subject_id=row.subject_id,
        person_id=row.person_id,
        caregiver_id=row.caregiver_id,
        action="privacy_request.export_fulfilled",
        target_type="privacy_export_artifact",
        target_id=existing.id,
        occurred_at=now,
        outcome="fulfilled",
        request_id=row.request_id,
        correlation_id=row.correlation_id,
        policy_version="privacy-export-fulfilment-v1",
        record_metadata={
            "privacy_request_id": row.id,
            "archive_sha256": digest,
            "archive_size_bytes": len(archive_bytes),
            "document_count": len(manifest["document_files"]),
        },
    ))
    try:
        db.commit()
        db.refresh(row)
        db.refresh(existing)
    except Exception:
        db.rollback()
        raise
    return {
        **_serialize(row),
        "fulfilment": fulfilment,
        "download_path": f"/api/v1/privacy/requests/{row.id}/export",
        "idempotent_replay": False,
    }


@router.get("/{request_id}/export")
def download_privacy_export(request_id: str, request: Request, db: Session = Depends(get_db)):
    person_id, caregiver_id = _scope(request, db)
    row = db.query(DataSubjectRequest).filter(
        DataSubjectRequest.id == request_id,
        DataSubjectRequest.person_id == person_id,
        DataSubjectRequest.caregiver_id == caregiver_id,
        DataSubjectRequest.requester_id == caregiver_id,
        DataSubjectRequest.request_type == "export",
        DataSubjectRequest.request_status == "fulfilled",
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Export not found")
    artifact = db.query(PrivacyExportArtifact).filter(
        PrivacyExportArtifact.request_id == row.id,
        PrivacyExportArtifact.person_id == person_id,
        PrivacyExportArtifact.caregiver_id == caregiver_id,
    ).first()
    now = datetime.now(timezone.utc)
    artifact_expiry = artifact.expires_at if artifact is not None else None
    if artifact_expiry is not None and artifact_expiry.tzinfo is None:
        artifact_expiry = artifact_expiry.replace(tzinfo=timezone.utc)
    if artifact is None or artifact_expiry <= now:
        raise HTTPException(status_code=410, detail="Export archive is unavailable or has expired")
    if len(artifact.content) != artifact.size_bytes or hashlib.sha256(artifact.content).hexdigest() != artifact.sha256:
        raise HTTPException(status_code=500, detail="Export archive failed its integrity check")
    artifact.downloaded_at = now
    db.add(AuditLogEvent(
        actor_id=caregiver_id,
        subject_id=person_id,
        person_id=person_id,
        caregiver_id=caregiver_id,
        action="privacy_request.export_downloaded",
        target_type="privacy_export_artifact",
        target_id=artifact.id,
        occurred_at=now,
        outcome="allowed",
        request_id=row.request_id,
        correlation_id=row.correlation_id,
        policy_version="privacy-export-download-v1",
        record_metadata={"archive_sha256": artifact.sha256, "archive_size_bytes": artifact.size_bytes},
    ))
    db.commit()
    return Response(
        content=bytes(artifact.content),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{artifact.filename}"',
            "Cache-Control": "no-store, private",
            "Pragma": "no-cache",
            "X-Content-Type-Options": "nosniff",
            "ETag": f'"{artifact.sha256}"',
        },
    )


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
