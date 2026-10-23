"""Authenticated document text extraction for the Living Care Record.

Extraction is deliberately pre-cognitive: it returns source text and technical
quality metadata only. It does not diagnose, summarize, or authorize care.
"""
from __future__ import annotations

import hashlib
import io
import re
from pathlib import PurePath
from typing import Any

from datetime import datetime, timezone
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.care import CareEvent, Caregiver, Evidence
from app.models.document_asset import DocumentAsset
from app.services.situation import analyze_saved_care_event

router = APIRouter(tags=["document-extraction"])
MAX_DOCUMENT_BYTES = 10 * 1024 * 1024
MAX_PDF_PAGES = 10
TEXT_EXTENSIONS = {".txt", ".md", ".csv", ".json", ".log"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp"}
SUPPORTED_EXTENSIONS = TEXT_EXTENSIONS | IMAGE_EXTENSIONS | {".pdf"}
PDF_MAGIC = b"%PDF-"
LOW_CONFIDENCE_THRESHOLD = 0.65


def _ocr_image(image: Any) -> tuple[str, float | None]:
    try:
        import pytesseract
        from pytesseract import Output
    except ImportError as exc:
        raise RuntimeError("OCR dependencies are not installed on this service.") from exc

    data = pytesseract.image_to_data(image, output_type=Output.DICT, config="--psm 3")
    words: list[str] = []
    confidences: list[float] = []
    for word, raw_confidence in zip(data.get("text", []), data.get("conf", [])):
        word = str(word).strip()
        try:
            confidence = float(raw_confidence)
        except (TypeError, ValueError):
            continue
        if word and confidence >= 0:
            words.append(word)
            confidences.append(confidence / 100.0)
    if not words:
        return "", None
    return " ".join(words), sum(confidences) / len(confidences)


def extract_document_content(filename: str, content_type: str, data: bytes) -> dict[str, Any]:
    """Extract a bounded document without persisting raw binary content."""
    if not data:
        raise ValueError("The selected file is empty.")
    if len(data) > MAX_DOCUMENT_BYTES:
        raise ValueError("The selected file exceeds the 10 MiB limit.")

    extension = PurePath(filename or "").suffix.lower()
    if extension not in SUPPORTED_EXTENSIONS:
        raise ValueError("Supported formats are PDF, common image files, and plain text files.")
    digest = hashlib.sha256(data).hexdigest()
    warnings: list[str] = []
    confidence: float | None = None
    source = "text"

    if extension in TEXT_EXTENSIONS:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError("This text file is not valid UTF-8 and could not be read safely.") from exc
    elif extension == ".pdf":
        if not data.startswith(PDF_MAGIC):
            raise ValueError("The file extension says PDF, but its content is not a valid PDF.")
        try:
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(data), strict=True)
            if reader.is_encrypted:
                raise ValueError("This PDF is encrypted. Export an unlocked copy and try again.")
            pages = reader.pages
            if not pages:
                raise ValueError("This PDF has no readable pages.")
            if len(pages) > MAX_PDF_PAGES:
                warnings.append(f"Only the first {MAX_PDF_PAGES} of {len(pages)} pages were processed.")
            page_text = [(page.extract_text() or "").strip() for page in pages[:MAX_PDF_PAGES]]
            text = "\n\n".join(part for part in page_text if part)
            source = "pdf_text"
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError("This PDF could not be opened. The file may be damaged or unsupported.") from exc

        if not text.strip():
            try:
                import fitz
                from PIL import Image
                document = fitz.open(stream=data, filetype="pdf")
                if document.page_count > MAX_PDF_PAGES:
                    warnings.append(f"Only the first {MAX_PDF_PAGES} of {document.page_count} scanned pages were processed.")
                ocr_parts: list[str] = []
                page_confidences: list[float] = []
                for page in document[:MAX_PDF_PAGES]:
                    pixmap = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
                    image_obj = Image.open(io.BytesIO(pixmap.tobytes("png")))
                    page_text_value, page_confidence = _ocr_image(image_obj)
                    if page_text_value:
                        ocr_parts.append(page_text_value)
                    if page_confidence is not None:
                        page_confidences.append(page_confidence)
                document.close()
                text = "\n\n".join(ocr_parts)
                confidence = sum(page_confidences) / len(page_confidences) if page_confidences else None
                source = "pdf_ocr"
            except RuntimeError as exc:
                raise ValueError("This PDF appears to be scanned, but OCR is unavailable on this service.") from exc
            except Exception as exc:
                raise ValueError("Text could not be extracted from this scanned PDF.") from exc
    else:
        try:
            from PIL import Image
            image_obj = Image.open(io.BytesIO(data))
            image_obj.verify()
            image_obj = Image.open(io.BytesIO(data))
            text, confidence = _ocr_image(image_obj)
            source = "image_ocr"
        except RuntimeError as exc:
            raise ValueError("OCR is unavailable on this service. Try a text-based PDF or text file.") from exc
        except Exception as exc:
            raise ValueError("This image could not be read. Try a clearer JPG, PNG, or TIFF image.") from exc

    text = re.sub(r"\x00", "", text).strip()
    if not text:
        raise ValueError("No readable text was found. The document may be blank, damaged, or too unclear.")
    if confidence is not None and confidence < LOW_CONFIDENCE_THRESHOLD:
        warnings.append("OCR confidence is low. Check the extracted text against the original before relying on it.")
    if len(text) > 500_000:
        text = text[:500_000]
        warnings.append("Extracted text was truncated at 500,000 characters.")
    return {
        "ok": True,
        "document": {
            "id": f"doc_{digest[:24]}",
            "name": PurePath(filename).name[:255],
            "mime_type": content_type or "application/octet-stream",
            "extracted_text": text,
            "extracted_text_preview": text[:600],
            "extracted_char_count": len(text),
            "ocr_confidence": confidence,
            "extraction_source": source,
            "extraction_warnings": warnings,
            "sha256": digest,
        },
    }


@router.post("/documents/extract")
async def extract_document(request: Request, file: UploadFile = File(...)):
    # Session-scope middleware sets these only after validating the bearer token.
    if not getattr(request.state, "scoped_person_id", None) or not getattr(request.state, "scoped_caregiver_id", None):
        raise HTTPException(status_code=401, detail="An authenticated care-record session is required.")
    try:
        data = await file.read(MAX_DOCUMENT_BYTES + 1)
        result = extract_document_content(file.filename or "upload", file.content_type or "", data)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        await file.close()
    return result


def _classify_document_type(text: str, filename: str) -> str:
    lowered = text.casefold()
    if any(term in lowered for term in ("discharge summary", "discharge instructions", "discharged from")):
        return "discharge_instructions"
    if any(term in lowered for term in ("medication list", "prescription", "drug name", "dosage")):
        return "medication_document"
    if any(term in lowered for term in ("laboratory result", "lab results", "reference range", "test result")):
        return "test_result"
    if any(term in lowered for term in ("care plan", "care instructions", "treatment plan")):
        return "care_plan"
    if any(term in lowered for term in ("appointment", "follow-up visit", "clinic visit")):
        return "appointment_document"
    suffix = PurePath(filename).suffix.lower()
    return "pdf_document" if suffix == ".pdf" else ("image_document" if suffix in IMAGE_EXTENSIONS else "text_document")


def _document_date_candidate(text: str) -> str | None:
    patterns = (
        r"\b(20\d{2}-\d{2}-\d{2})\b",
        r"\b(\d{1,2}/\d{1,2}/20\d{2})\b",
        r"\b(\d{1,2}[- ](?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*[- ,]+20\d{2})\b",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1)
    return None


@router.post("/documents/intake", status_code=201)
async def intake_document(request: Request, file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Extract and persist both original source bytes and review-only text candidates."""
    person_id = getattr(request.state, "scoped_person_id", None)
    caregiver_id = getattr(request.state, "scoped_caregiver_id", None)
    if not person_id or not caregiver_id:
        raise HTTPException(status_code=401, detail="An authenticated care-record session is required.")

    caregiver = db.query(Caregiver).filter(
        Caregiver.id == caregiver_id,
        Caregiver.person_id == person_id,
    ).first()
    if caregiver is None:
        raise HTTPException(status_code=403, detail="The authenticated caregiver is not linked to this care record.")

    data = await file.read(MAX_DOCUMENT_BYTES + 1)
    filename = PurePath(file.filename or "care-document").name[:255]
    content_type = (file.content_type or "application/octet-stream").lower().strip()
    if len(data) > MAX_DOCUMENT_BYTES:
        raise HTTPException(status_code=413, detail="The selected file exceeds the 10 MiB limit.")
    try:
        extraction = extract_document_content(filename, content_type, data)
        document = extraction["document"]
        extracted_text = document["extracted_text"]
        extraction_source = document["extraction_source"]
        ocr_confidence = document["ocr_confidence"]
        warnings = document["extraction_warnings"]
        extraction_status = "extracted"
        extraction_error = None
        digest = document["sha256"]
    except ValueError as exc:
        # Unsupported types, empty files, and oversized files are rejected. A supported
        # but unreadable/scanned file is still retained with an explicit extraction failure.
        suffix = PurePath(filename).suffix.lower()
        if suffix not in SUPPORTED_EXTENSIONS or not data or len(data) > MAX_DOCUMENT_BYTES:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        digest = hashlib.sha256(data).hexdigest()
        extracted_text = ""
        extraction_source = "failed"
        ocr_confidence = None
        warnings = [str(exc)]
        extraction_status = "pending_ocr" if "OCR is unavailable" in str(exc) else "extraction_failed"
        extraction_error = str(exc)

    document_type = _classify_document_type(extracted_text, filename)
    document_date = _document_date_candidate(extracted_text) if extracted_text else None
    metadata = {
        "original_filename": filename,
        "mime_type": content_type,
        "size_bytes": len(data),
        "sha256": digest,
        "document_type_candidate": document_type,
        "document_date_candidate": document_date,
        "document_date_review_status": "pending_review" if document_date else "not_detected",
        "extraction_status": extraction_status,
        "extraction_source": extraction_source,
        "extraction_version": "document-intake-v1",
        "ocr_confidence": ocr_confidence,
        "extraction_warnings": warnings,
        "extraction_error": extraction_error,
        "extracted_char_count": len(extracted_text),
        "review_status": "pending_review",
        "source_file_preserved": True,
    }
    evidence = Evidence(
        person_id=person_id,
        type="document",
        source_text=extracted_text if extracted_text else "Document source preserved; readable text is not currently available. No document facts have been verified.",
        original_file_path="pending",
        uploaded_by_caregiver_id=caregiver_id,
        extra_metadata=__import__("json").dumps(metadata, ensure_ascii=False),
        confidence=ocr_confidence,
        evidence_status="uploaded",
        source_type="document_upload",
    )
    db.add(evidence)
    db.flush()
    evidence.original_file_path = f"db://document-assets/{evidence.id}"
    db.add(DocumentAsset(
        evidence_id=evidence.id,
        person_id=person_id,
        uploaded_by_caregiver_id=caregiver_id,
        original_filename=filename,
        content_type=content_type,
        size_bytes=len(data),
        sha256=digest,
        content=data,
    ))
    now = datetime.now(timezone.utc)
    event = CareEvent(
        person_id=person_id,
        event_type="document_intake",
        status="recorded",
        occurred_at=now,
        occurred_at_timezone=caregiver.timezone,
        time_provenance="document_upload_time_not_document_date",
        title=f"Document added: {filename}"[:200],
        description=(
            f"Document uploaded. Text extraction status: {extraction_status}. "
            "Detected type/date and extracted content are unverified candidates pending caregiver review."
        ),
        evidence_ids=[evidence.id],
        created_by_caregiver_id=caregiver_id,
        tags=[f"document_type:{document_type}", f"document_extraction:{extraction_status}"],
    )
    db.add(event)
    db.commit()
    db.refresh(evidence)
    db.refresh(event)

    analysis = analyze_saved_care_event(db, event.id) if extracted_text else None
    return {
        "evidence_id": evidence.id,
        "care_event_id": event.id,
        "person_id": person_id,
        "filename": filename,
        "content_type": content_type,
        "size_bytes": len(data),
        "sha256": digest,
        "document_type_candidate": document_type,
        "document_date_candidate": document_date,
        "document_date_review_status": metadata["document_date_review_status"],
        "extraction_status": extraction_status,
        "extraction_source": extraction_source,
        "extraction_version": metadata["extraction_version"],
        "ocr_confidence": ocr_confidence,
        "extraction_warnings": warnings,
        "extraction_error": extraction_error,
        "extracted_char_count": len(extracted_text),
        "source_file_preserved": True,
        "review_required": True,
        "qwen_extraction_status": analysis["qwen_extraction_status"] if analysis else "not_run_no_text",
        "qwen_candidates": analysis["qwen_candidates"] if analysis else None,
        "qwen_analysis_id": analysis.get("qwen_analysis_id") if analysis else None,
    }


@router.get("/documents/{evidence_id}/file")
def download_document_file(evidence_id: str, request: Request, db: Session = Depends(get_db)):
    person_id = getattr(request.state, "scoped_person_id", None)
    if not person_id:
        raise HTTPException(status_code=401, detail="An authenticated care-record session is required.")
    asset = db.query(DocumentAsset).filter(
        DocumentAsset.evidence_id == evidence_id,
        DocumentAsset.person_id == person_id,
    ).first()
    if asset is None:
        raise HTTPException(status_code=404, detail="Document file not found for this care record.")
    filename = quote(asset.original_filename, safe="")
    return Response(
        content=asset.content,
        media_type=asset.content_type,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{filename}",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )
