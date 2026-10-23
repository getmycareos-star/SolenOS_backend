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
from app.schemas.capture import CaptureCandidateReviewInput, DailyCaptureInput, DailyCaptureOutput, DailyCaptureRecordOutput, UploadOutput

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
        qwen_analysis_id=analysis.get("qwen_analysis_id"),
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
        "qwen_analysis_id": analysis.get("qwen_analysis_id"),
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



MAX_DOCUMENT_UPLOAD_BYTES = 20 * 1024 * 1024
ALLOWED_DOCUMENT_TYPES = {
    "application/pdf",
    "image/jpeg",
    "image/png",
    "image/webp",
}


def _document_type_from_text(text: str, content_type: str) -> str:
    lowered = text.casefold()
    if any(term in lowered for term in ("discharge summary", "discharge instructions", "discharged from")):
        return "discharge_instructions"
    if any(term in lowered for term in ("prescription", "medication list", "dosage", "take one tablet")):
        return "medication_document"
    if any(term in lowered for term in ("laboratory result", "lab results", "reference range", "test result")):
        return "test_result"
    if any(term in lowered for term in ("appointment", "follow-up visit", "clinic visit")):
        return "appointment_document"
    if any(term in lowered for term in ("care plan", "care instructions", "treatment plan")):
        return "care_plan"
    if content_type == "application/pdf":
        return "pdf_document"
    return "image_document"


def _document_date_from_text(text: str) -> str | None:
    patterns = (
        r"\\b(20\\d{2}-\\d{2}-\\d{2})\\b",
        r"\\b(\\d{1,2}/\\d{1,2}/20\\d{2})\\b",
        r"\\b(\\d{1,2}[- ](?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*[- ,]+20\\d{2})\\b",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def _extract_document_text(content: bytes, content_type: str) -> tuple[str, str, str | None]:
    """Extract text when a local parser/OCR engine is available; never invent text."""
    if content_type == "application/pdf":
        try:
            from pypdf import PdfReader

            reader = PdfReader(BytesIO(content), strict=False)
            if len(reader.pages) > 100:
                raise HTTPException(status_code=413, detail="PDF exceeds the 100-page intake limit")
            text = "\\n".join((page.extract_text() or "") for page in reader.pages).strip()
            return text[:100_000], "pdf_text_extraction", None if text else "no_text_detected"
        except HTTPException:
            raise
        except Exception:
            return "", "pdf_text_extraction", "extraction_failed"
    try:
        import pytesseract

        with Image.open(BytesIO(content)) as image:
            image = image.convert("RGB")
            text = pytesseract.image_to_string(image).strip()
            return text[:100_000], "tesseract_ocr", None if text else "no_text_detected"
    except ImportError:
        return "", "ocr_unavailable", "pending_ocr"
    except Exception:
        return "", "tesseract_ocr", "ocr_failed"


def _document_signature_matches(content_type: str, content: bytes) -> bool:
    if content_type == "application/pdf":
        return content.startswith(b"%PDF-")
    return _image_signature_matches(content_type, content)


@router.post("/documents/intake", status_code=201)
def intake_document(
    file: UploadFile = File(...),
    person_id: str = Form(...),
    caregiver_id: str = Form(...),
    extracted_text: str = Form(default=""),
    extraction_source: str = Form(default="server"),
    db: Session = Depends(get_db),
    account: AuthAccount = Depends(current_account),
):
    """Persist an uploaded document, its extraction provenance, and review-only candidates."""
    assert_person_access(db, account.id, person_id, "write")
    caregiver = _verified_caregiver(db, account, person_id, caregiver_id)
    content_type = (file.content_type or "").lower().strip()
    if content_type not in ALLOWED_DOCUMENT_TYPES:
        raise HTTPException(status_code=415, detail="Upload a PDF, JPEG, PNG, or WebP document")
    content = file.file.read(MAX_DOCUMENT_UPLOAD_BYTES + 1)
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded document is empty")
    if len(content) > MAX_DOCUMENT_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Document exceeds the 20 MiB upload limit")
    if not _document_signature_matches(content_type, content):
        raise HTTPException(status_code=415, detail="File content does not match its declared type")

    if content_type.startswith("image/"):
        expected_format = {"image/jpeg": "JPEG", "image/png": "PNG", "image/webp": "WEBP"}[content_type]
        try:
            with Image.open(BytesIO(content)) as image:
                if image.format != expected_format:
                    raise HTTPException(status_code=415, detail="Image content does not match its declared type")
                width, height = image.size
                if width < 1 or height < 1 or width * height > 40_000_000:
                    raise HTTPException(status_code=413, detail="Image dimensions exceed the allowed limit")
                image.verify()
        except HTTPException:
            raise
        except (UnidentifiedImageError, OSError, ValueError):
            raise HTTPException(status_code=415, detail="Uploaded file is not a valid supported image")

    raw_filename = (file.filename or "care-document").replace("\\\\", "/").split("/")[-1]
    safe_filename = re.sub(r"[^A-Za-z0-9._ -]", "_", raw_filename).strip(" .")[:255] or "care-document"
    digest = hashlib.sha256(content).hexdigest()
    supplied_text = extracted_text.strip()
    if len(supplied_text) > 100_000:
        raise HTTPException(status_code=413, detail="Extracted text exceeds the 100,000-character limit")
    if supplied_text:
        document_text = supplied_text
        engine = extraction_source[:80] if extraction_source.strip() else "client_extraction"
        extraction_status = "extracted"
    else:
        document_text, engine, extraction_error = _extract_document_text(content, content_type)
        extraction_status = "extracted" if document_text else (extraction_error or "pending_extraction")

    detected_type = _document_type_from_text(document_text, content_type)
    detected_date = _document_date_from_text(document_text) if document_text else None
    metadata = {
        "original_filename": safe_filename,
        "mime_type": content_type,
        "size_bytes": len(content),
        "sha256": digest,
        "document_type": detected_type,
        "document_date_candidate": detected_date,
        "document_date_review_status": "pending_review" if detected_date else "not_detected",
        "extraction_status": extraction_status,
        "extraction_engine": engine,
        "extraction_version": "document-intake-v1",
        "extracted_character_count": len(document_text),
        "review_status": "pending_review",
        "source_file_preserved": True,
    }
    evidence = EvidenceModel(
        person_id=person_id,
        type="document",
        source_text=document_text if document_text else "Document stored; text extraction is pending or unavailable. No document facts have been verified.",
        original_file_path="pending",
        uploaded_by_caregiver_id=caregiver.id,
        extra_metadata=json.dumps(metadata, ensure_ascii=False),
        confidence=None,
        evidence_status="uploaded",
        source_type="document_upload",
    )
    db.add(evidence)
    db.flush()
    evidence.original_file_path = f"db://evidence-files/{evidence.id}"
    db.add(EvidenceFile(
        evidence_id=evidence.id,
        uploaded_by_account_id=account.id,
        original_filename=safe_filename,
        content_type=content_type,
        size_bytes=len(content),
        sha256=digest,
        content=content,
    ))
    event = CareEventModel(
        person_id=person_id,
        event_type="document_intake",
        status="recorded",
        occurred_at=datetime.now(timezone.utc),
        occurred_at_timezone=caregiver.timezone,
        title=f"Document added: {safe_filename}"[:200],
        description=(
            f"Document uploaded. Extraction status: {extraction_status}. "
            "Detected type/date and extracted facts are candidates for caregiver review, not verified facts."
        ),
        evidence_ids=[evidence.id],
        created_by_caregiver_id=caregiver.id,
        tags=[f"document_type:{detected_type}", f"document_extraction:{extraction_status}"],
    )
    db.add(event)
    db.commit()
    db.refresh(evidence)
    db.refresh(event)

    analysis = None
    if document_text:
        analysis = analyze_saved_care_event(db, event.id)
    return {
        "evidence_id": evidence.id,
        "care_event_id": event.id,
        "person_id": person_id,
        "filename": safe_filename,
        "content_type": content_type,
        "size_bytes": len(content),
        "sha256": digest,
        "document_type_candidate": detected_type,
        "document_date_candidate": detected_date,
        "document_date_review_status": metadata["document_date_review_status"],
        "extraction_status": extraction_status,
        "extraction_engine": engine,
        "extraction_version": metadata["extraction_version"],
        "extracted_character_count": len(document_text),
        "source_file_preserved": True,
        "review_required": True,
        "qwen_extraction_status": analysis["qwen_extraction_status"] if analysis else "not_run_no_text",
        "qwen_candidates": analysis["qwen_candidates"] if analysis else None,
        "qwen_analysis_id": analysis.get("qwen_analysis_id") if analysis else None,
    }


@router.get("/documents/{evidence_id}")
def get_document_intake_record(
    evidence_id: str,
    db: Session = Depends(get_db),
    account: AuthAccount = Depends(current_account),
):
    evidence = db.query(EvidenceModel).filter(
        EvidenceModel.id == evidence_id,
        EvidenceModel.type == "document",
    ).first()
    if evidence is None:
        raise HTTPException(status_code=404, detail="Document evidence not found")
    assert_person_access(db, account.id, evidence.person_id, "read")
    try:
        metadata = json.loads(evidence.extra_metadata or "{}")
    except (TypeError, json.JSONDecodeError):
        metadata = {}
    return {
        "evidence_id": evidence.id,
        "person_id": evidence.person_id,
        "source_text": evidence.source_text,
        "metadata": metadata,
        "evidence_status": evidence.evidence_status,
        "source_type": evidence.source_type,
    }


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



@router.post("/capture/analyses/{analysis_id}/review")
def review_capture_candidate(
    analysis_id: str,
    payload: CaptureCandidateReviewInput,
    db: Session = Depends(get_db),
    account: AuthAccount = Depends(current_account),
):
    """Persist an explicit caregiver review decision while preserving the original candidate.

    This endpoint does not promote extracted content into authoritative LCR facts.
    """
    analysis = db.query(CaptureAnalysis).filter(CaptureAnalysis.id == analysis_id).first()
    if analysis is None:
        raise HTTPException(status_code=404, detail="Capture analysis not found")
    assert_person_access(db, account.id, analysis.person_id, "write")

    data = analysis.candidate_data
    if not isinstance(data, dict) or not isinstance(data.get("observations"), list):
        raise HTTPException(status_code=409, detail="This analysis has no reviewable candidates")
    observations = list(data["observations"])
    if payload.candidate_index >= len(observations):
        raise HTTPException(status_code=404, detail="Candidate index not found")
    candidate = observations[payload.candidate_index]
    if not isinstance(candidate, dict):
        raise HTTPException(status_code=409, detail="Candidate data is invalid")
    if payload.decision == "correct" and not (
        (payload.corrected_observation or "").strip()
        or (payload.corrected_subject or "").strip()
    ):
        raise HTTPException(
            status_code=422,
            detail="Provide a corrected subject or observation when choosing correct",
        )

    from copy import deepcopy
    from datetime import datetime, timezone

    original = deepcopy(candidate)
    now = datetime.now(timezone.utc).isoformat()
    if payload.decision == "accept":
        candidate["review_status"] = "accepted_by_caregiver"
    elif payload.decision == "reject":
        candidate["review_status"] = "rejected_by_caregiver"
    else:
        candidate["review_status"] = "corrected_by_caregiver"
        if payload.corrected_subject is not None and payload.corrected_subject.strip():
            candidate["subject"] = payload.corrected_subject.strip()
        if payload.corrected_observation is not None and payload.corrected_observation.strip():
            candidate["observation"] = payload.corrected_observation.strip()

    candidate["reviewed_at"] = now
    candidate["reviewed_by_account_id"] = account.id
    observations[payload.candidate_index] = candidate
    history = list(data.get("review_history") or [])
    history.append({
        "candidate_index": payload.candidate_index,
        "decision": payload.decision,
        "reviewed_at": now,
        "reviewed_by_account_id": account.id,
        "original_candidate": original,
        "corrected_subject": payload.corrected_subject,
        "corrected_observation": payload.corrected_observation,
        "note": payload.note.strip() if payload.note else None,
    })
    updated_data = dict(data)
    updated_data["observations"] = observations
    updated_data["review_history"] = history
    updated_data["review_notice"] = (
        "Caregiver review is preserved as a review decision. It does not independently verify "
        "the underlying report or write an authoritative Living Care Record fact."
    )
    analysis.candidate_data = updated_data
    db.commit()
    db.refresh(analysis)
    return {
        "analysis_id": analysis.id,
        "candidate_index": payload.candidate_index,
        "candidate": observations[payload.candidate_index],
        "review_history": history,
        "authoritative_lcr_write": False,
        "message": "Review decision saved with the original candidate preserved. The Living Care Record was not changed.",
    }
