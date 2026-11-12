"""Authenticated privacy request intake, review and guarded fulfilment workflows."""
import base64
import hashlib
import hmac
import io
import json
import os
import re
import uuid
import zipfile
from datetime import datetime, timezone, timedelta
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import Response
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import MetaData, Table, inspect as sa_inspect, select, text
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import Base, get_db
from app.models.anonymous_session import AnonymousSession
from app.models.privacy_delivery import AuditLogEvent
from app.models.privacy_request import DataSubjectRequest
from app.models.care import Correction, Insight, Evidence, CareEvent, Person
from app.models.collaboration import EvidenceUpload, ConversationSession, ConversationMessage
from app.models.privacy_export import PrivacyExportArtifact

router = APIRouter(prefix="/privacy/requests", tags=["privacy requests"])
MAX_PRIVACY_EXPORT_BYTES = 100 * 1024 * 1024

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
    correction_target_type: Optional[Literal["insight", "care_event_description", "evidence_source"]] = None
    correction_target_id: Optional[str] = Field(default=None, max_length=255)
    correction_text: Optional[str] = Field(default=None, max_length=10000)
    correction_reason: Optional[str] = Field(default=None, max_length=2000)


class PrivacyRequestReview(BaseModel):
    decision: Literal["approve", "reject"]
    review_basis: str = Field(min_length=10, max_length=1000)
    authority_evidence_checked: Optional[str] = Field(default=None, max_length=1000)


class PrivacyDeletionFulfilment(BaseModel):
    backup_erasure_verified: bool
    backup_retention_evidence: str = Field(min_length=20, max_length=2000)


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

def _privacy_deletion_reviewer(
    token: Optional[str] = Header(default=None, alias="X-SolenOS-Privacy-Deletion-Reviewer-Token"),
) -> str:
    """Require a separately provisioned reviewer identity for destructive erasure."""
    expected = os.getenv("SOLENOS_PRIVACY_DELETION_REVIEWER_TOKEN", "").strip()
    reviewer_id = os.getenv("SOLENOS_PRIVACY_DELETION_REVIEWER_ID", "").strip()
    primary_token = os.getenv("SOLENOS_PRIVACY_REVIEWER_TOKEN", "").strip()
    primary_id = os.getenv("SOLENOS_PRIVACY_REVIEWER_ID", "").strip()
    if (
        len(expected) < 32
        or not reviewer_id
        or len(reviewer_id) > 120
        or not primary_id
        or reviewer_id == primary_id
        or (primary_token and hmac.compare_digest(expected, primary_token))
    ):
        raise HTTPException(
            status_code=503,
            detail="Independent privacy deletion reviewer access is not configured",
        )
    if not token or not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=401, detail="Invalid independent deletion reviewer credentials")
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

    correction_snapshot = None
    if payload.request_type == "correction" and payload.correction_target_id:
        if payload.correction_target_type == "insight":
            target = db.query(Insight).filter(
                Insight.id == payload.correction_target_id,
                Insight.person_id == person_id,
            ).first()
        elif payload.correction_target_type == "care_event_description":
            target = db.query(CareEvent).filter(
                CareEvent.id == payload.correction_target_id,
                CareEvent.person_id == person_id,
            ).first()
        else:
            target = db.query(Evidence).filter(
                Evidence.id == payload.correction_target_id,
                Evidence.person_id == person_id,
            ).first()
        if target is None:
            raise HTTPException(status_code=404, detail="Correction target was not found in this care record")
        correction_snapshot = {
            "target_type": payload.correction_target_type,
            "target_id": target.id,
            "corrected_text": payload.correction_text,
            "reason": payload.correction_reason,
            "original_value": (
                target.source_text if payload.correction_target_type == "evidence_source"
                else target.description or ""
            ),
            "target_updated_at": (
                target.source_text_sha256 if payload.correction_target_type == "evidence_source"
                else target.updated_at.isoformat() if getattr(target, "updated_at", None) else None
            ),
        }

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
            "correction": correction_snapshot,
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
    expired_count = db.query(PrivacyExportArtifact).filter(
        PrivacyExportArtifact.expires_at <= datetime.now(timezone.utc)
    ).delete(synchronize_session=False)
    if expired_count:
        db.commit()
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


def _privacy_export_cipher():
    key = os.getenv("SOLENOS_PRIVACY_EXPORT_ENCRYPTION_KEY", "").strip()
    if not key:
        raise HTTPException(status_code=503, detail="Privacy export encryption is not configured")
    try:
        return Fernet(key.encode("ascii"))
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Privacy export encryption key is invalid") from exc


def _build_caregiver_export(db: Session, person_id: str, caregiver_id: str) -> tuple[bytes, dict]:
    """Build a deterministic export of person records and caregiver-attributed data."""
    archive_buffer = io.BytesIO()
    manifest = {
        "format": "solenos-privacy-export-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": {"person_id": person_id, "requesting_caregiver_id": caregiver_id},
        "record_sets": {},
        "document_files": [],
        "unexported_tables": [],
        "exclusions": [
            "Rows explicitly attributed to a different caregiver are excluded.",
            "Actor, requester and generic created_by identifiers are not treated as caregiver ownership; tables without a reliable caregiver or person scope are listed as unexported.",
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
    )
    with zipfile.ZipFile(archive_buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for table in Base.metadata.sorted_tables:
            name = table.name
            if name in {"privacy_export_artifacts", "share_intakes", "caregiver_accounts", "recovery_code_sets", "anonymous_sessions"}:
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
            else:
                owner_column = next((column for column in owner_columns if column in columns), None)
                if owner_column:
                    predicate = table.c[owner_column] == caregiver_id
                else:
                    manifest["unexported_tables"].append({
                        "table": name,
                        "reason": "No recognized person or caregiver ownership column; excluded to prevent cross-user disclosure.",
                    })
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
            if archive_buffer.tell() > MAX_PRIVACY_EXPORT_BYTES:
                raise HTTPException(status_code=413, detail="Privacy export exceeds the 100 MiB archive limit")

        # Include migration-created tables not registered in the ORM when their
        # ownership can be established from person_id or caregiver attribution.
        # Record unscopable tables in the manifest rather than silently implying
        # that every database table was included.
        inspector = sa_inspect(db.get_bind())
        registered_tables = set(Base.metadata.tables)
        excluded_raw_tables = {"alembic_version", "legal_holds"}
        for raw_name in inspector.get_table_names():
            if raw_name in registered_tables or raw_name in excluded_raw_tables:
                continue
            raw_table = sa_inspect(db.get_bind()).get_columns(raw_name)
            raw_columns = {column["name"] for column in raw_table}
            reflected = Table(raw_name, MetaData(), autoload_with=db.get_bind())
            if "person_id" in raw_columns:
                predicate = reflected.c.person_id == person_id
                raw_owner = next((column for column in owner_columns if column in raw_columns), None)
                if raw_owner:
                    predicate = predicate & (reflected.c[raw_owner] == caregiver_id)
            else:
                raw_owner = next((column for column in owner_columns if column in raw_columns), None)
                if raw_owner:
                    predicate = reflected.c[raw_owner] == caregiver_id
                else:
                    manifest["unexported_tables"].append({
                        "table": raw_name,
                        "reason": "No recognized person or caregiver ownership column; excluded to prevent cross-user disclosure.",
                    })
                    continue
            rows = db.execute(reflected.select().where(predicate)).mappings().all()
            if rows:
                records = [
                    {key: _export_value(value) for key, value in row.items()}
                    for row in rows
                ]
                archive.writestr(
                    f"records/{re.sub(r'[^a-zA-Z0-9_.-]', '_', raw_name)}.json",
                    json.dumps(records, ensure_ascii=False, sort_keys=True, default=str),
                )
                manifest["record_sets"][raw_name] = len(records)
                if archive_buffer.tell() > MAX_PRIVACY_EXPORT_BYTES:
                    raise HTTPException(status_code=413, detail="Privacy export exceeds the 100 MiB archive limit")

        # Conversation messages do not carry person_id themselves; scope them
        # through the caregiver-owned conversation sessions.
        conversation_ids = [
            item[0] for item in db.query(ConversationSession.id).filter(
                ConversationSession.person_id == person_id,
                ConversationSession.caregiver_id == caregiver_id,
            ).all()
        ]
        if conversation_ids:
            messages = db.query(ConversationMessage).filter(
                ConversationMessage.session_id.in_(conversation_ids)
            ).order_by(ConversationMessage.created_at.asc(), ConversationMessage.id.asc()).all()
            archive.writestr(
                "records/conversation_messages.json",
                json.dumps([
                    {column.name: _export_value(getattr(message, column.name, None)) for column in ConversationMessage.__table__.columns}
                    for message in messages
                ], ensure_ascii=False, sort_keys=True, default=str),
            )
            manifest["record_sets"]["conversation_messages"] = len(messages)

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
            if archive_buffer.tell() > MAX_PRIVACY_EXPORT_BYTES:
                raise HTTPException(status_code=413, detail="Privacy export exceeds the 100 MiB archive limit")
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
    db.query(PrivacyExportArtifact).filter(PrivacyExportArtifact.expires_at <= now).delete(synchronize_session=False)
    db.flush()
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

    cipher = _privacy_export_cipher()
    archive_bytes, manifest = _build_caregiver_export(db, str(row.person_id), str(row.caregiver_id))
    expires_at = now + timedelta(hours=24)
    filename = f"solenos-privacy-export-{row.id}.zip"
    digest = hashlib.sha256(archive_bytes).hexdigest()
    encrypted_archive = cipher.encrypt(archive_bytes)
    if existing is None:
        existing = PrivacyExportArtifact(
            id=str(uuid.uuid4()),
            request_id=row.id,
            person_id=str(row.person_id),
            caregiver_id=str(row.caregiver_id),
            filename=filename,
            content=encrypted_archive,
            size_bytes=len(archive_bytes),
            sha256=digest,
            expires_at=expires_at,
        )
        db.add(existing)
    else:
        existing.content = encrypted_archive
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
        if artifact is not None:
            db.delete(artifact)
            db.commit()
        raise HTTPException(status_code=410, detail="Export archive is unavailable or has expired")
    try:
        archive_bytes = _privacy_export_cipher().decrypt(bytes(artifact.content))
    except (InvalidToken, ValueError) as exc:
        raise HTTPException(status_code=500, detail="Export archive could not be decrypted") from exc
    if len(archive_bytes) != artifact.size_bytes or hashlib.sha256(archive_bytes).hexdigest() != artifact.sha256:
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
        content=archive_bytes,
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
    target_type = correction_request.get("target_type")
    if target_type not in {"insight", "care_event_description", "evidence_source"}:
        raise HTTPException(status_code=422, detail="Unsupported correction target")
    target_id = str(correction_request.get("target_id") or "").strip()
    corrected_text = str(correction_request.get("corrected_text") or "").strip()
    if not target_id or not corrected_text:
        raise HTTPException(status_code=422, detail="Correction target and corrected text are required")

    if target_type == "insight":
        target = (
            db.query(Insight)
            .filter(Insight.id == target_id, Insight.person_id == row.person_id)
            .with_for_update()
            .first()
        )
        persisted_target_type = "insight"
    elif target_type == "care_event_description":
        target = (
            db.query(CareEvent)
            .filter(CareEvent.id == target_id, CareEvent.person_id == row.person_id)
            .with_for_update()
            .first()
        )
        persisted_target_type = "care_event"
    else:
        target = (
            db.query(Evidence)
            .filter(Evidence.id == target_id, Evidence.person_id == row.person_id)
            .with_for_update()
            .first()
        )
        persisted_target_type = "evidence_source"
    if target is None:
        raise HTTPException(status_code=404, detail="Correction target was not found in this care record")
    original_text = (
        target.source_text if persisted_target_type == "evidence_source"
        else target.description or ""
    )
    requested_original = str(correction_request.get("original_value") or "")
    requested_updated_at = correction_request.get("target_updated_at")
    current_updated_at = (
        target.source_text_sha256 if persisted_target_type == "evidence_source"
        else target.updated_at.isoformat() if getattr(target, "updated_at", None) else None
    )
    if original_text != requested_original or current_updated_at != requested_updated_at:
        raise HTTPException(
            status_code=409,
            detail="The correction target changed after submission; a fresh correction request and review are required.",
        )
    if original_text == corrected_text:
        raise HTTPException(status_code=409, detail="The requested correction does not change the target")

    now = datetime.now(timezone.utc)
    correction = Correction(
        id=str(uuid.uuid4()),
        person_id=row.person_id,
        target_type=persisted_target_type,
        target_id=target.id,
        original_text=original_text or "",
        corrected_text=corrected_text,
        reason=str(correction_request.get("reason") or "Approved privacy correction request"),
        caregiver_id=row.caregiver_id,
    )
    db.add(correction)
    # Evidence source text is immutable. For that target, the correction is
    # retained as an append-only annotation linked by target_type/target_id;
    # the original evidence bytes/text and fingerprint remain unchanged.
    if persisted_target_type != "evidence_source":
        target.description = corrected_text
        target.updated_at = now
    row.request_status = "fulfilled"
    row.resolved_at = now
    row.resolution_code = "correction_fulfilled"
    fulfilment = {
        "operation": "correction",
        "correction_id": correction.id,
        "target_type": persisted_target_type,
        "target_id": target.id,
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
            "target_type": persisted_target_type,
            "target_id": target.id,
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


@router.post("/{request_id}/fulfil-deletion")
def fulfil_approved_deletion(
    request_id: str,
    payload: PrivacyDeletionFulfilment,
    reviewer_id: str = Depends(_privacy_deletion_reviewer),
    db: Session = Depends(get_db),
):
    """Erase a single-caregiver record after independent approval and backup-expiry review."""
    row = (
        db.query(DataSubjectRequest)
        .filter(DataSubjectRequest.id == request_id)
        .with_for_update()
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Privacy request not found")
    if row.request_type != "deletion":
        raise HTTPException(status_code=409, detail="This privacy request is not a deletion")
    metadata = row.record_metadata if isinstance(row.record_metadata, dict) else {}
    existing = metadata.get("fulfilment") if isinstance(metadata.get("fulfilment"), dict) else {}
    if row.request_status == "fulfilled" and existing.get("operation") == "deletion":
        return {
            **_serialize(row),
            "fulfilment": existing,
            "idempotent_replay": True,
        }
    if row.request_status != "approved_pending_fulfilment_assessment":
        raise HTTPException(status_code=409, detail="Deletion request has not been approved")
    review = metadata.get("review") if isinstance(metadata.get("review"), dict) else {}
    approving_reviewer = str(review.get("reviewer_id") or "")
    if not approving_reviewer or approving_reviewer == reviewer_id:
        raise HTTPException(
            status_code=409,
            detail="Deletion fulfilment requires a second reviewer independent of the approval decision.",
        )
    if payload.backup_erasure_verified is not True:
        raise HTTPException(
            status_code=409,
            detail="Primary deletion is blocked until backups containing this data are verified expired or otherwise irrecoverable.",
        )
    backup_evidence_hash = hashlib.sha256(
        payload.backup_retention_evidence.strip().encode("utf-8")
    ).hexdigest()
    person_id = str(row.person_id or "")
    caregiver_id = str(row.caregiver_id or "")
    if not person_id or not caregiver_id:
        raise HTTPException(status_code=409, detail="Deletion scope is incomplete")

    # A shared record contains other caregivers' personal data and contributions.
    # Until mixed-subject erasure is separately assessed, fail closed rather than
    # silently erase other caregivers' accounts or private material.
    from app.models.care import Caregiver, Person
    linked_caregivers = db.query(Caregiver.id).filter(Caregiver.person_id == person_id).all()
    if {str(item[0]) for item in linked_caregivers} != {caregiver_id}:
        raise HTTPException(
            status_code=409,
            detail="This care record has multiple linked caregivers; mixed-caregiver deletion requires a separate scope assessment.",
        )

    # Capture evidence primary keys before generic FK cleanup can null or otherwise
    # alter a nullable person_id. Erasure is verified by immutable row identity,
    # not only by a mutable ownership column.
    evidence_ids_to_erase = db.execute(
        select(Evidence.id).where(Evidence.person_id == person_id)
    ).scalars().all()
    evidence_upload_ids_to_erase = db.execute(
        select(EvidenceUpload.evidence_id)
        .join(Evidence, Evidence.id == EvidenceUpload.evidence_id)
        .where(Evidence.person_id == person_id)
    ).scalars().all()
    if not evidence_upload_ids_to_erase and evidence_ids_to_erase:
        evidence_upload_ids_to_erase = db.execute(
            select(EvidenceUpload.evidence_id).where(
                EvidenceUpload.evidence_id.in_(evidence_ids_to_erase)
            )
        ).scalars().all()

    # Inspect the active request transaction. Engine-level inspection can
    # check out the same SQLite StaticPool connection and roll back pending
    # erasure statements when the inspection connection closes.
    bind = db.connection()
    inspector = sa_inspect(bind)
    if inspector.has_table("legal_holds"):
        holds = db.execute(
            text("SELECT hold_status FROM legal_holds WHERE subject_id = :subject_id"),
            {"subject_id": person_id},
        ).all()
        terminal_hold_states = {"released", "expired", "closed", "cancelled", "inactive"}
        blocking = [item[0] for item in holds if str(item[0] or "").strip().lower() not in terminal_hold_states]
        if blocking:
            raise HTTPException(
                status_code=409,
                detail="Deletion is blocked by an active or unclassified legal hold.",
            )

    # Build the directly scoped deletion set from registered ORM tables. The
    # foreign-key closure also captures dependent rows without person_id columns
    # (for example document bytes and conversation messages).
    tables = list(Base.metadata.sorted_tables)
    excluded_tables = {"data_subject_requests", "audit_log_events"}
    scoped_ids: dict[str, set] = {}
    direct_predicates = {}
    owner_columns = (
        "caregiver_id",
        "created_by_caregiver_id",
        "uploaded_by_caregiver_id",
        "reported_by_caregiver_id",
        "collected_by_caregiver_id",
        "changed_by_caregiver_id",
        "updated_by_caregiver_id",
    )
    for table in tables:
        if table.name in excluded_tables or not table.primary_key.columns:
            continue
        columns = set(table.c.keys())
        predicate = None
        if table.name == "persons":
            predicate = table.c.id == person_id
        elif "person_id" in columns:
            predicate = table.c.person_id == person_id
        else:
            owner = next((name for name in owner_columns if name in columns), None)
            if owner:
                predicate = table.c[owner] == caregiver_id
        if predicate is None:
            continue
        direct_predicates[table.name] = predicate
        primary_key = list(table.primary_key.columns)
        if len(primary_key) == 1:
            values = db.execute(
                select(primary_key[0]).select_from(table).where(predicate)
            ).scalars().all()
            if values:
                scoped_ids[table.name] = set(values)

    changed = True
    while changed:
        changed = False
        for child in tables:
            if child.name in excluded_tables or not child.primary_key.columns:
                continue
            primary_key = list(child.primary_key.columns)
            if len(primary_key) != 1:
                continue
            for foreign_key in child.foreign_keys:
                parent_name = foreign_key.column.table.name
                parent_ids = scoped_ids.get(parent_name, set())
                if not parent_ids:
                    continue
                child_column = foreign_key.parent
                values = db.execute(
                    select(primary_key[0]).select_from(child).where(child_column.in_(parent_ids))
                ).scalars().all()
                if not values:
                    continue
                current = scoped_ids.setdefault(child.name, set())
                before = len(current)
                current.update(values)
                if len(current) != before:
                    changed = True

    now = datetime.now(timezone.utc)
    # Delete in reverse FK dependency order. Core deletes intentionally bypass
    # append-only ORM listeners: erasure is an explicit, reviewed privacy action.
    try:
        # Remove legacy lifecycle tables created by generic migrations but not
        # represented by ORM models. Keep the canonical request, audit and legal
        # hold records for explicit redaction/retention handling below.
        known_tables = set(Base.metadata.tables)
        protected_tables = {"data_subject_requests", "audit_log_events", "legal_holds"}
        raw_tables = [
            name for name in inspector.get_table_names()
            if name not in known_tables and name not in protected_tables
        ]

        # Build a dependency depth from the two subject roots through legacy
        # tables. Delete deepest dependants first so ON DELETE SET NULL cannot
        # leave indirectly linked personal data behind. Composite foreign keys
        # are handled by matching every constrained/referred column pair.
        raw_foreign_keys = {
            name: inspector.get_foreign_keys(name) for name in raw_tables
        }
        reachable_depth = {"persons": 0, "caregivers": 0}
        dependency_graph_converged = False
        for _ in range(len(raw_tables) + 1):
            changed_depth = False
            for child_name, foreign_keys in raw_foreign_keys.items():
                for foreign_key in foreign_keys:
                    parent_name = foreign_key.get("referred_table")
                    if parent_name not in reachable_depth:
                        continue
                    candidate_depth = reachable_depth[parent_name] + 1
                    if child_name not in reachable_depth or candidate_depth > reachable_depth[child_name]:
                        reachable_depth[child_name] = candidate_depth
                        changed_depth = True
            if not changed_depth:
                dependency_graph_converged = True
                break
        if not dependency_graph_converged:
            raise HTTPException(
                status_code=409,
                detail="Legacy data dependency cycle prevents a safe deletion order; request was not fulfilled.",
            )

        for raw_table_name in sorted(
            raw_tables,
            key=lambda name: reachable_depth.get(name, -1),
            reverse=True,
        ):
            safe_name = raw_table_name.replace('"', '""')
            raw_columns = {
                item["name"] for item in inspector.get_columns(raw_table_name)
            }

            # Direct ownership is authoritative for legacy tables that carry
            # their own subject scope.
            if "person_id" in raw_columns:
                db.execute(
                    text(f'DELETE FROM "{safe_name}" WHERE person_id = :person_id'),
                    {"person_id": person_id},
                )
            elif "caregiver_id" in raw_columns:
                db.execute(
                    text(f'DELETE FROM "{safe_name}" WHERE caregiver_id = :caregiver_id'),
                    {"caregiver_id": caregiver_id},
                )

            # Also remove rows linked through any reachable parent table.
            # This runs before that parent is deleted and supports composite FKs.
            for foreign_key in raw_foreign_keys.get(raw_table_name, []):
                parent_name = foreign_key.get("referred_table")
                constrained = foreign_key.get("constrained_columns") or []
                referred = foreign_key.get("referred_columns") or []
                if (
                    parent_name not in reachable_depth
                    or not constrained
                    or len(constrained) != len(referred)
                ):
                    continue
                child_terms = []
                for child_column, parent_column in zip(constrained, referred):
                    safe_child_column = str(child_column).replace('"', '""')
                    safe_parent_column = str(parent_column).replace('"', '""')
                    child_terms.append(
                        f'child."{safe_child_column}" = parent."{safe_parent_column}"'
                    )
                safe_parent_name = str(parent_name).replace('"', '""')
                join_predicate = " AND ".join(child_terms)
                db.execute(
                    text(
                        f'DELETE FROM "{safe_name}" AS child '
                        f'WHERE EXISTS (SELECT 1 FROM "{safe_parent_name}" AS parent '
                        f'WHERE {join_predicate})'
                    )
                )

        # Redact any earlier requests for the same care record, retaining only
        # request type, timestamps and a non-reversible per-request pseudonym.
        prior_requests = db.query(DataSubjectRequest).filter(
            DataSubjectRequest.person_id == person_id,
            DataSubjectRequest.id != row.id,
        ).all()
        for prior in prior_requests:
            prior.requester_id = "erased:" + hashlib.sha256(
                f"{prior.id}:{person_id}".encode("utf-8")
            ).hexdigest()
            prior.actor_id = None
            prior.subject_id = None
            prior.person_id = None
            prior.caregiver_id = None
            prior.record_metadata = {"redacted_for_privacy_erasure": True}

        # The document-review history is append-only for ordinary operations.
        # A separately reviewed privacy erasure may delete it only inside this
        # transaction; the database trigger rejects all other deletes and every update.
        if bind.dialect.name == "postgresql":
            db.execute(text("SET LOCAL solenos.privacy_erasure = 'on'"))

        for table in reversed(tables):
            if table.name in excluded_tables:
                continue
            primary_key = list(table.primary_key.columns)
            ids = scoped_ids.get(table.name, set())
            if len(primary_key) == 1 and ids:
                db.execute(table.delete().where(primary_key[0].in_(ids)))
            elif table.name in direct_predicates:
                db.execute(table.delete().where(direct_predicates[table.name]))

        # Explicitly erase evidence payloads by the primary keys captured before
        # the generic FK cleanup. A nullable person_id may be cleared during that
        # pass, so re-querying only by person_id can falsely report successful
        # erasure while the evidence row itself survives.
        if evidence_ids_to_erase:
            db.execute(EvidenceUpload.__table__.delete().where(
                EvidenceUpload.evidence_id.in_(evidence_ids_to_erase)
            ))
        if evidence_upload_ids_to_erase:
            db.execute(EvidenceUpload.__table__.delete().where(
                EvidenceUpload.evidence_id.in_(evidence_upload_ids_to_erase)
            ))
        db.execute(
            text("DELETE FROM evidence_uploads WHERE evidence_id IN (SELECT id FROM evidence WHERE person_id = :person_id)"),
            {"person_id": person_id},
        )
        remaining_uploads = db.execute(
            select(EvidenceUpload.evidence_id).where(
                EvidenceUpload.evidence_id.in_(set(evidence_ids_to_erase) | set(evidence_upload_ids_to_erase))
            )
        ).scalars().all() if (evidence_ids_to_erase or evidence_upload_ids_to_erase) else []
        if remaining_uploads:
            raise HTTPException(status_code=409, detail="Evidence upload bytes remain after the care-record deletion pass; request was not fulfilled")
        # Also mark any remaining ORM evidence objects for deletion. This
        # prevents a loaded identity-map row from surviving a bulk/Core delete path.
        evidence_rows = db.query(Evidence).filter(Evidence.person_id == person_id).all()
        evidence_rows_by_id = {str(item.id): item for item in evidence_rows}
        for evidence_id in evidence_ids_to_erase:
            row_by_id = evidence_rows_by_id.get(str(evidence_id))
            if row_by_id is None:
                row_by_id = db.query(Evidence).filter(Evidence.id == evidence_id).first()
            if row_by_id is not None:
                db.delete(row_by_id)
        db.flush()
        # Use direct SQL as a final deterministic erasure pass. Evidence can have
        # nullable ownership changes from dependent cleanup, so delete both by the
        # original scoped owner and by the immutable IDs captured before cleanup.
        db.execute(
            text("DELETE FROM evidence WHERE person_id = :person_id"),
            {"person_id": person_id},
        )
        for evidence_id in evidence_ids_to_erase:
            db.execute(
                text("DELETE FROM evidence WHERE id = :evidence_id"),
                {"evidence_id": str(evidence_id)},
            )
        remaining_evidence_query = db.query(Evidence.id)
        if evidence_ids_to_erase:
            remaining_evidence_query = remaining_evidence_query.filter(
                Evidence.id.in_(evidence_ids_to_erase)
            )
        else:
            remaining_evidence_query = remaining_evidence_query.filter(
                Evidence.person_id == person_id
            )
        if remaining_evidence_query.count():
            raise HTTPException(
                status_code=409,
                detail="Evidence rows remain after the care-record deletion pass; request was not fulfilled",
            )

        # Explicitly remove the two root identity rows after the dependent-row
        # closure. Foreign-key violations fail closed rather than reporting a
        # successful erasure while either root identity remains.
        db.execute(text("DELETE FROM caregivers WHERE id = :caregiver_id"), {"caregiver_id": caregiver_id})
        db.execute(text("DELETE FROM persons WHERE id = :person_id"), {"person_id": person_id})
        # Verify the root identity is actually gone before marking the request
        # fulfilled. The ORM delete is a typed fallback for database drivers
        # that do not match the raw UUID/string bind exactly.
        remaining_person = db.query(Person).filter(Person.id == person_id).first()
        if remaining_person is not None:
            db.query(Person).filter(Person.id == person_id).delete(synchronize_session=False)
        if db.query(Person).filter(Person.id == person_id).count() != 0:
            raise HTTPException(
                status_code=409,
                detail="Person record remains after deletion attempt; request was not marked fulfilled",
            )
        remaining_caregiver = db.query(Caregiver).filter(Caregiver.id == caregiver_id).first()
        if remaining_caregiver is not None:
            db.query(Caregiver).filter(Caregiver.id == caregiver_id).delete(synchronize_session=False)
        if db.query(Caregiver).filter(Caregiver.id == caregiver_id).count() != 0:
            raise HTTPException(
                status_code=409,
                detail="Caregiver record remains after deletion attempt; request was not marked fulfilled",
            )

        # Released/expired holds are no longer a deletion blocker and must not
        # retain the deleted subject identifier.
        if inspector.has_table("legal_holds"):
            db.execute(
                text("DELETE FROM legal_holds WHERE subject_id = :subject_id"),
                {"subject_id": person_id},
            )

        # Redact existing audit records now, but do not mark this request fulfilled
        # until a separate post-commit check proves both root identities are gone.
        # If a database-side dependency leaves either root row behind, the request
        # remains approved and can be retried without a false completed receipt.
        db.query(AuditLogEvent).filter(
            AuditLogEvent.person_id == person_id
        ).update(
            {
                AuditLogEvent.actor_id: None,
                AuditLogEvent.subject_id: None,
                AuditLogEvent.person_id: None,
                AuditLogEvent.caregiver_id: None,
                AuditLogEvent.target_id: None,
                AuditLogEvent.record_metadata: {"redacted_for_privacy_erasure": True},
            },
            synchronize_session=False,
        )
        db.commit()

        # Verify after commit. Retry each root deletion in its own transaction
        # before creating the immutable fulfilled receipt.
        db.expire_all()
        if db.query(Person).filter(Person.id == person_id).count() != 0:
            db.execute(
                text("DELETE FROM persons WHERE id = :person_id"),
                {"person_id": person_id},
            )
            db.commit()
            db.expire_all()
        if db.query(Person).filter(Person.id == person_id).count() != 0:
            raise HTTPException(
                status_code=409,
                detail="Person record remains after committed deletion verification",
            )

        if db.query(Caregiver).filter(Caregiver.id == caregiver_id).count() != 0:
            db.execute(
                text("DELETE FROM caregivers WHERE id = :caregiver_id"),
                {"caregiver_id": caregiver_id},
            )
            db.commit()
            db.expire_all()
        if db.query(Caregiver).filter(Caregiver.id == caregiver_id).count() != 0:
            raise HTTPException(
                status_code=409,
                detail="Caregiver record remains after committed deletion verification",
            )
        if evidence_upload_ids_to_erase:
            remaining_uploads = db.query(EvidenceUpload.evidence_id).filter(
                EvidenceUpload.evidence_id.in_(evidence_upload_ids_to_erase)
            ).all()
            if remaining_uploads:
                raise HTTPException(status_code=409, detail="Evidence upload bytes remain after committed deletion verification")

        # Verify evidence by the immutable IDs captured before erasure, after
        # commit. This catches database-side ownership nulling or transaction
        # behavior that a pre-commit person_id query cannot detect.
        remaining_evidence_ids = []
        if evidence_ids_to_erase:
            remaining_evidence_ids = [
                str(item[0]) for item in db.query(Evidence.id).filter(
                    Evidence.id.in_(evidence_ids_to_erase)
                ).all()
            ]
        if remaining_evidence_ids:
            for evidence_id in remaining_evidence_ids:
                db.execute(
                    text("DELETE FROM evidence WHERE id = :evidence_id"),
                    {"evidence_id": str(evidence_id)},
                )
            db.commit()
            db.expire_all()
            remaining_evidence_ids = [
                str(item[0]) for item in db.query(Evidence.id).filter(
                    Evidence.id.in_(evidence_ids_to_erase)
                ).all()
            ]
        if remaining_evidence_ids:
            raise HTTPException(
                status_code=409,
                detail="Evidence rows remain after committed deletion verification; request was not marked fulfilled",
            )

        # Both root identities are now absent. Only now persist the redacted
        # request receipt and terminal audit event.
        pseudonym = "erased:" + hashlib.sha256(
            f"{request_id}:{person_id}:{caregiver_id}".encode("utf-8")
        ).hexdigest()
        receipt_id = str(uuid.uuid4())
        row.requester_id = pseudonym
        row.actor_id = None
        row.subject_id = None
        row.person_id = None
        row.caregiver_id = None
        row.request_status = "fulfilled"
        row.resolved_at = now
        row.resolution_code = "deletion_fulfilled"
        fulfilment = {
            "operation": "deletion",
            "receipt_id": receipt_id,
            "fulfilled_at": now.isoformat(),
            "fulfilled_by": reviewer_id,
            "scope": "single-caregiver care record",
            "verification": "registered person-scoped tables and foreign-key dependants removed; active legal holds rejected; reviewer attested backups containing the data are expired or irrecoverable",
            "backup_erasure_verified": True,
            "backup_retention_evidence_sha256": backup_evidence_hash,
        }
        row.record_metadata = {"fulfilment": fulfilment}
        db.add(AuditLogEvent(
            actor_id=reviewer_id,
            action="privacy_request.deletion_fulfilled",
            target_type="privacy_deletion_receipt",
            target_id=receipt_id,
            occurred_at=now,
            outcome="fulfilled",
            request_id=row.request_id,
            correlation_id=row.correlation_id,
            policy_version="privacy-deletion-fulfilment-v1",
            record_metadata={
                "privacy_request_id": request_id,
                "scope": "single-caregiver care record",
                "backup_erasure_verified": True,
                "backup_retention_evidence_sha256": backup_evidence_hash,
                "redacted": True,
            },
        ))
        # Repeat root erasure after every dependent cleanup and audit write. This
        # is deliberately the final database mutation in the transaction so no
        # later lifecycle write can reintroduce a root row through a trigger or
        # relationship side effect.
        db.execute(
            text("DELETE FROM caregivers WHERE id = :caregiver_id"),
            {"caregiver_id": caregiver_id},
        )
        db.execute(
            text("DELETE FROM persons WHERE id = :person_id"),
            {"person_id": person_id},
        )
        remaining_roots = db.execute(
            text("""
                SELECT
                    (SELECT COUNT(*) FROM persons WHERE id = :person_id) AS persons_remaining,
                    (SELECT COUNT(*) FROM caregivers WHERE id = :caregiver_id) AS caregivers_remaining
            """),
            {"person_id": person_id, "caregiver_id": caregiver_id},
        ).mappings().one()
        if int(remaining_roots["persons_remaining"]) or int(remaining_roots["caregivers_remaining"]):
            raise HTTPException(
                status_code=409,
                detail="Care record identity rows remain after the final deletion pass; request was not fulfilled",
            )

        db.commit()
        db.expire_all()
        # Verify from the committed database state before returning a success receipt.
        committed_roots = db.execute(
            text("""
                SELECT
                    (SELECT COUNT(*) FROM persons WHERE id = :person_id) AS persons_remaining,
                    (SELECT COUNT(*) FROM caregivers WHERE id = :caregiver_id) AS caregivers_remaining
            """),
            {"person_id": person_id, "caregiver_id": caregiver_id},
        ).mappings().one()
        if int(committed_roots["persons_remaining"]) or int(committed_roots["caregivers_remaining"]):
            raise HTTPException(
                status_code=409,
                detail="Care record identity rows remain after committed deletion verification",
            )
        db.refresh(row)
    except Exception:
        db.rollback()
        raise
    return {
        **_serialize(row),
        "fulfilment": fulfilment,
        "idempotent_replay": False,
    }
