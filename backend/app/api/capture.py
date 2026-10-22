"""Authenticated daily capture and image evidence endpoints."""
import hashlib
import re
import json
from io import BytesIO
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from PIL import Image, UnidentifiedImageError
from sqlalchemy.orm import Session

from app.api.authentication import current_account
from app.core.authz import assert_account_is_caregiver, assert_person_access
from app.core.database import get_db
from app.models.auth_identity import AuthAccount
from app.models.care import CareEvent as CareEventModel, Caregiver as CaregiverModel, Evidence as EvidenceModel
from app.models.capture import DailyCapture, EvidenceFile
from app.models.capture_analysis import CaptureAnalysis
from app.services.situation import analyze_saved_care_event
from app.schemas.capture import DailyCaptureInput, DailyCaptureOutput, DailyCaptureRecordOutput, UploadOutput

router = APIRouter(tags=["daily-capture"], dependencies=[Depends(current_account)])
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}


def _image_signature_matches(content_type: str, content: bytes) -> bool:
    if content_type == "image/png":
        return content.startswith(b"\x89PNG\r\n\x1a\n")
    if content_type == "image/jpeg":
        return content.startswith(b"\xff\xd8\xff")
    if content_type == "image/webp":
        return len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP"
    return False


def _verified_caregiver(db: Session, account: AuthAccount, person_id: str, caregiver_id: str) -> CaregiverModel:
    caregiver = db.query(CaregiverModel).filter(
        CaregiverModel.id == caregiver_id,
        CaregiverModel.person_id == person_id,
    ).first()
    if caregiver is None:
        raise HTTPException(status_code=400, detail="Caregiver must belong to this care record")
    assert_account_is_caregiver(db, account.id, caregiver.id, person_id)
    return caregiver


@router.post("/daily/log", response_model=DailyCaptureOutput, status_code=201)
def save_daily_capture(
    payload: DailyCaptureInput,
    db: Session = Depends(get_db),
    account: AuthAccount = Depends(current_account),
):
    assert_person_access(db, account.id, payload.person_id, "write")
    caregiver = _verified_caregiver(db, account, payload.person_id, payload.caregiver_id)

    photo_ids = {
        item.photo_evidence_id
        for item in payload.medications
        if item.photo_evidence_id
    }
    if photo_ids:
        found = {
            row[0]
            for row in db.query(EvidenceModel.id).filter(
                EvidenceModel.id.in_(photo_ids),
                EvidenceModel.person_id == payload.person_id,
            ).all()
        }
        if found != photo_ids:
            raise HTTPException(status_code=400, detail="Medication photo evidence must belong to this care record")

    recorded_at = datetime.now(timezone.utc)
    capture = DailyCapture(
        person_id=payload.person_id,
        caregiver_id=caregiver.id,
        recorded_by_account_id=account.id,
        medications=[item.model_dump(mode="json") for item in payload.medications],
        vitals=[item.model_dump(mode="json") for item in payload.vitals],
        scales=[item.model_dump(mode="json") for item in payload.scales],
        notes=payload.notes,
        location=payload.location.model_dump(mode="json") if payload.location else None,
        location_provenance=payload.location_provenance if payload.location else None,
        recorded_at=recorded_at,
    )
    db.add(capture)
    db.flush()

    structured_source = {
        "medications": [item.model_dump(mode="json") for item in payload.medications],
        "vitals": [item.model_dump(mode="json") for item in payload.vitals],
        "scales": [item.model_dump(mode="json") for item in payload.scales],
        "notes": payload.notes,
        "location": payload.location.model_dump(mode="json") if payload.location else None,
        "location_provenance": payload.location_provenance if payload.location else None,
    }
    structured_evidence = EvidenceModel(
        person_id=payload.person_id,
        type="structured_daily_capture",
        source_text=json.dumps(structured_source, ensure_ascii=False, default=str),
        uploaded_by_caregiver_id=caregiver.id,
        time_provenance=recorded_at.isoformat(),
        confidence=None,
        evidence_status="reported",
        source_type="caregiver_structured_capture",
    )
    db.add(structured_evidence)
    db.flush()

    summary = (
        f"Daily capture recorded: {len(payload.medications)} medication entries, "
        f"{len(payload.vitals)} vital measurements, and {len(payload.scales)} wellbeing scores."
    )
    if payload.location is not None:
        summary += " A user-provided location was attached."
    description = f"{summary}\nCaregiver notes: {payload.notes}" if payload.notes.strip() else summary
    event = CareEventModel(
        person_id=payload.person_id,
        event_type="daily_capture",
        status="recorded",
        occurred_at=recorded_at,
        occurred_at_timezone=caregiver.timezone,
        title="Daily care capture",
        description=description,
        evidence_ids=sorted(photo_ids | {structured_evidence.id}),
        created_by_caregiver_id=caregiver.id,
        tags=[
            f"daily_capture_id:{capture.id}",
            f"medication_entries:{len(payload.medications)}",
            f"vital_measurements:{len(payload.vitals)}",
            f"wellbeing_scores:{len(payload.scales)}",
        ],
    )
    db.add(event)
    db.commit()
    db.refresh(capture)
    db.refresh(event)

    # Structured Daily Capture and free-text capture share the same deterministic
    # change/situation analysis. The response reports partial success explicitly.
    analysis = analyze_saved_care_event(db, event.id)
    return DailyCaptureOutput(
        success=True,
        daily_capture_id=capture.id,
        care_event_id=event.id,
        recorded_at=capture.recorded_at or recorded_at,
        analysis_status=analysis["analysis_status"],
        analysis_error=analysis["analysis_error"],
        what_changed=analysis["what_changed"],
        attention_candidate=analysis["attention_candidate"],
        follow_up_candidate=analysis["follow_up_candidate"],
        situations=analysis["situations"],
        qwen_extraction_status=analysis["qwen_extraction_status"],
        qwen_candidates=analysis["qwen_candidates"],
    )


@router.post("/documents/{care_event_id}/analyze")
def analyze_text_document_in_care_event(
    care_event_id: str,
    db: Session = Depends(get_db),
    account: AuthAccount = Depends(current_account),
):
    """Re-run Qwen extraction for text-bearing document evidence already linked to an event."""
    event = db.query(CareEventModel).filter(CareEventModel.id == care_event_id).first()
    if event is None:
        raise HTTPException(status_code=404, detail="Care event not found")
    assert_person_access(db, account.id, event.person_id, "write")
    evidence_ids = [item for item in (event.evidence_ids or []) if isinstance(item, str)]
    linked = db.query(EvidenceModel).filter(
        EvidenceModel.person_id == event.person_id,
        EvidenceModel.id.in_(evidence_ids),
    ).all() if evidence_ids else []
    document_rows = [
        item for item in linked
        if item.type == "document" and isinstance(item.source_text, str)
        and item.source_text.strip()
        and "text content has not been extracted" not in item.source_text.lower()
    ]
    if not document_rows:
        raise HTTPException(
            status_code=422,
            detail="This care event has no text-extracted document evidence. Image OCR/document text extraction must complete before Qwen can analyze it.",
        )
    analysis = analyze_saved_care_event(db, event.id)
    return {
        "care_event_id": event.id,
        "document_evidence_ids": [item.id for item in document_rows],
        "qwen_extraction_status": analysis["qwen_extraction_status"],
        "qwen_candidates": analysis["qwen_candidates"],
        "analysis_status": analysis["analysis_status"],
        "read_only_to_lcr": True,
        "review_required": True,
    }


@router.get("/capture/analyses/{person_id}")
def list_capture_analyses(
    person_id: str,
    limit: int = Query(default=10, ge=1, le=30),
    db: Session = Depends(get_db),
    account: AuthAccount = Depends(current_account),
):
    """Return persisted Qwen extraction candidates only to accounts with record access."""
    assert_person_access(db, account.id, person_id, "read")
    rows = (
        db.query(CaptureAnalysis)
        .filter(CaptureAnalysis.person_id == person_id)
        .order_by(CaptureAnalysis.created_at.desc())
        .limit(limit)
        .all()
    )
    return {
        "items": [
            {
                "id": row.id,
                "care_event_id": row.care_event_id,
                "person_id": row.person_id,
                "status": row.status,
                "provider_status": row.provider_status,
                "model_name": row.model_name,
                "candidate_data": row.candidate_data,
                "source_evidence_ids": row.source_evidence_ids or [],
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ]
    }


@router.get("/daily/log/{person_id}", response_model=list[DailyCaptureRecordOutput])
def list_daily_captures(
    person_id: str,
    limit: int = Query(default=30, ge=1, le=100),
    db: Session = Depends(get_db),
    account: AuthAccount = Depends(current_account),
):
    """Read persisted daily captures only for a care record the account may access."""
    assert_person_access(db, account.id, person_id, "read")
    return (
        db.query(DailyCapture)
        .filter(DailyCapture.person_id == person_id)
        .order_by(DailyCapture.recorded_at.desc())
        .limit(limit)
        .all()
    )


@router.post("/upload", response_model=UploadOutput, status_code=201)
def upload_image_evidence(
    file: UploadFile = File(...),
    person_id: str = Form(...),
    caregiver_id: str = Form(...),
    db: Session = Depends(get_db),
    account: AuthAccount = Depends(current_account),
):
    assert_person_access(db, account.id, person_id, "write")
    caregiver = _verified_caregiver(db, account, person_id, caregiver_id)
    content_type = (file.content_type or "").lower().strip()
    if content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(status_code=415, detail="Only JPEG, PNG, and WebP images are accepted")
    content = file.file.read(MAX_UPLOAD_BYTES + 1)
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Image exceeds the 10 MiB upload limit")
    if not _image_signature_matches(content_type, content):
        raise HTTPException(status_code=415, detail="Image content does not match its declared file type")
    expected_format = {"image/jpeg": "JPEG", "image/png": "PNG", "image/webp": "WEBP"}[content_type]
    try:
        with Image.open(BytesIO(content)) as image:
            if image.format != expected_format:
                raise HTTPException(status_code=415, detail="Image content does not match its declared file type")
            width, height = image.size
            if width < 1 or height < 1 or width * height > 40_000_000:
                raise HTTPException(status_code=413, detail="Image dimensions exceed the allowed limit")
            image.verify()
    except HTTPException:
        raise
    except (UnidentifiedImageError, OSError, ValueError):
        raise HTTPException(status_code=415, detail="Uploaded file is not a valid supported image")

    raw_filename = (file.filename or "care-image").replace("\\", "/").split("/")[-1]
    safe_filename = re.sub(r"[^A-Za-z0-9._ -]", "_", raw_filename).strip(" .")[:255] or "care-image"
    digest = hashlib.sha256(content).hexdigest()
    evidence = EvidenceModel(
        person_id=person_id,
        type="image",
        source_text="Image uploaded. Text content has not been extracted.",
        original_file_path="pending",
        uploaded_by_caregiver_id=caregiver.id,
        source_type="image_upload",
        evidence_status="uploaded",
    )
    db.add(evidence)
    db.flush()
    evidence.original_file_path = f"db://evidence-files/{evidence.id}"
    stored_file = EvidenceFile(
        evidence_id=evidence.id,
        uploaded_by_account_id=account.id,
        original_filename=safe_filename,
        content_type=content_type,
        size_bytes=len(content),
        sha256=digest,
        content=content,
    )
    db.add(stored_file)
    db.commit()
    return UploadOutput(
        evidence_id=evidence.id,
        content_type=content_type,
        size_bytes=len(content),
        text_extracted=False,
        message="Image stored as evidence; OCR/text extraction has not been performed.",
    )


@router.get("/upload/{evidence_id}")
def download_image_evidence(
    evidence_id: str,
    db: Session = Depends(get_db),
    account: AuthAccount = Depends(current_account),
):
    evidence = db.query(EvidenceModel).filter(EvidenceModel.id == evidence_id).first()
    if evidence is None:
        raise HTTPException(status_code=404, detail="Evidence not found")
    assert_person_access(db, account.id, evidence.person_id, "read")
    stored_file = db.query(EvidenceFile).filter(EvidenceFile.evidence_id == evidence.id).first()
    if stored_file is None:
        raise HTTPException(status_code=404, detail="Stored image file not found")
    filename = quote(stored_file.original_filename, safe="")
    return Response(
        content=stored_file.content,
        media_type=stored_file.content_type,
        headers={
            "Content-Disposition": f"inline; filename*=UTF-8''{filename}",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )
