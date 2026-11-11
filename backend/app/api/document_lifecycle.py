"""Persist source-verified text extraction and local OCR against uploaded evidence."""
from __future__ import annotations

import hashlib
import io
from datetime import datetime, timezone

import pypdfium2 as pdfium
import pytesseract
from PIL import Image, ImageOps, UnidentifiedImageError

from fastapi import APIRouter, Depends, HTTPException, Request
from pypdf import PdfReader
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.anonymous_session import AnonymousSession
from app.models.care import Evidence
from app.models.capture_analysis import CaptureAnalysis
from app.models.collaboration import EvidenceUpload

router = APIRouter(prefix="/documents", tags=["document lifecycle"])
MAX_EXTRACTED_CHARS = 100_000
MAX_PDF_PAGES = 100
MAX_OCR_PAGES = 25
MAX_IMAGE_PIXELS = 20_000_000
Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS


def _identity(request: Request, db: Session) -> tuple[str, str]:
    person_id = getattr(request.state, "scoped_person_id", None)
    caregiver_id = getattr(request.state, "scoped_caregiver_id", None)
    if person_id and caregiver_id:
        return str(person_id), str(caregiver_id)
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(status_code=401, detail="A valid SolenOS care session is required")
    token_hash = hashlib.sha256(token.strip().encode("utf-8")).hexdigest()
    session = db.query(AnonymousSession).filter(
        AnonymousSession.token_hash == token_hash,
        AnonymousSession.revoked_at.is_(None),
        AnonymousSession.expires_at > datetime.now(timezone.utc),
    ).first()
    if session is None:
        raise HTTPException(status_code=401, detail="Care session is invalid or expired")
    return str(session.person_id), str(session.caregiver_id)


def _verified_content(upload: EvidenceUpload) -> bytes:
    """Verify the persisted original before both first extraction and cached retries."""
    content = bytes(upload.content)
    if len(content) != upload.size_bytes or hashlib.sha256(content).hexdigest() != upload.sha256:
        raise HTTPException(status_code=500, detail="Stored evidence failed its integrity check")
    return content


def _ocr_image(image: Image.Image) -> str:
    """Run the installed local Tesseract engine; never silently fall back to a fake result."""
    try:
        normalized = ImageOps.exif_transpose(image).convert("RGB")
        return pytesseract.image_to_string(normalized, lang="eng", config="--psm 6").strip()
    except pytesseract.TesseractNotFoundError as exc:
        raise HTTPException(
            status_code=503,
            detail="The local OCR engine is not installed; the original document remains preserved.",
        ) from exc
    except pytesseract.TesseractError as exc:
        raise HTTPException(status_code=422, detail="OCR could not read this document image") from exc


def _extract(upload: EvidenceUpload, content: bytes | None = None) -> tuple[str, str]:
    content = _verified_content(upload) if content is None else content
    mime = (upload.mime_type or "").split(";", 1)[0].lower()
    filename = (upload.filename or "").lower()
    if mime == "application/pdf" or filename.endswith(".pdf"):
        try:
            reader = PdfReader(io.BytesIO(content), strict=True)
            if len(reader.pages) > MAX_PDF_PAGES:
                raise HTTPException(status_code=413, detail="PDFs are limited to 100 pages")
            native_pages = [(page.extract_text() or "").strip() for page in reader.pages]
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=422, detail="The saved PDF could not be read") from exc

        if native_pages and all(native_pages):
            page_texts = [f"[Page {index + 1}]\n{text}" for index, text in enumerate(native_pages)]
            return "\n\n".join(page_texts)[:MAX_EXTRACTED_CHARS], "server_pdf_text"

        ocr_page_indexes = [index for index, value in enumerate(native_pages) if not value]
        if len(ocr_page_indexes) > MAX_OCR_PAGES:
            raise HTTPException(
                status_code=413,
                detail=f"Scanned PDF pages are limited to {MAX_OCR_PAGES} per extraction.",
            )
        rendered = None
        try:
            rendered = pdfium.PdfDocument(content)
            page_texts = []
            for index, native_text in enumerate(native_pages):
                if native_text:
                    page_texts.append(f"[Page {index + 1}]\n{native_text}")
                    continue
                page = rendered[index]
                try:
                    width, height = page.get_size()
                    if width * height * 4 > MAX_IMAGE_PIXELS:
                        raise HTTPException(
                            status_code=413,
                            detail=f"PDF page {index + 1} exceeds the 20-megapixel OCR limit.",
                        )
                    bitmap = page.render(scale=2)
                    image = bitmap.to_pil()
                    try:
                        ocr_text = _ocr_image(image)
                    finally:
                        image.close()
                        bitmap.close()
                    if ocr_text:
                        page_texts.append(f"[Page {index + 1} OCR]\n{ocr_text}")
                    else:
                        # Preserve the page boundary and make the extraction gap explicit.
                        # This marker is never passed off as extracted clinical content.
                        page_texts.append(f"[Page {index + 1} OCR]\n[No readable text detected on this page]")
                finally:
                    page.close()
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=422, detail="The saved PDF page could not be rendered for OCR") from exc
        finally:
            if rendered is not None:
                rendered.close()
        extracted = "\n\n".join(page_texts).strip()
        if not extracted:
            raise HTTPException(
                status_code=422,
                detail="No readable text was found after OCR. The original document remains preserved.",
            )
        method = "server_pdf_text_plus_tesseract_ocr" if any(native_pages) else "server_tesseract_ocr_pdf"
        return extracted[:MAX_EXTRACTED_CHARS], method

    if mime.startswith("image/") or filename.endswith((".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp")):
        try:
            with Image.open(io.BytesIO(content)) as image:
                if image.width * image.height > MAX_IMAGE_PIXELS:
                    raise HTTPException(status_code=413, detail="Images are limited to 20 megapixels for OCR")
                extracted = _ocr_image(image)
        except HTTPException:
            raise
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
            raise HTTPException(status_code=422, detail="The saved image could not be decoded for OCR") from exc
        if not extracted:
            raise HTTPException(
                status_code=422,
                detail="No readable text was found in the image. The original document remains preserved.",
            )
        return f"[Image OCR]\n[Page 1 OCR]\n{extracted}"[:MAX_EXTRACTED_CHARS], "server_tesseract_ocr_image"

    if mime.startswith("text/") or mime in {"application/json", "application/xml", "application/yaml"} or filename.endswith(
        (".txt", ".md", ".csv", ".json", ".xml", ".yaml", ".yml", ".log")
    ):
        try:
            extracted = content.decode("utf-8-sig").strip()
        except UnicodeDecodeError as exc:
            raise HTTPException(status_code=415, detail="Saved text document is not UTF-8") from exc
        if not extracted:
            raise HTTPException(status_code=422, detail="The saved document contains no readable text")
        return extracted[:MAX_EXTRACTED_CHARS], "server_utf8_text"

    raise HTTPException(
        status_code=415,
        detail="Supported formats are UTF-8 text, text-based or scanned PDF, and common image files.",
    )



def _verify_persisted_extraction(
    analysis: CaptureAnalysis,
    upload: EvidenceUpload,
    db: Session,
    content: bytes | None = None,
) -> bytes:
    """Verify binary and extracted-text integrity without re-running OCR on every read."""
    content = _verified_content(upload) if content is None else content
    binary_digest = hashlib.sha256(content).hexdigest()
    metadata = analysis.candidates if isinstance(analysis.candidates, dict) else {}
    if metadata.get("kind") != "document_extraction_v1" or metadata.get("binary_sha256") != binary_digest:
        raise HTTPException(status_code=409, detail="The extraction no longer matches the preserved original document")

    source_text = (analysis.source_text or "").strip()
    source_digest = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
    stored_digest = metadata.get("source_text_sha256")
    if stored_digest:
        if stored_digest != source_digest:
            raise HTTPException(status_code=409, detail="The persisted extraction text changed after extraction")
    else:
        # Backfill the digest for older persisted rows exactly once. Re-run OCR
        # only for this legacy case, then persist the integrity marker.
        original_text, _ = _extract(upload, content)
        if original_text.strip() != source_text:
            raise HTTPException(status_code=409, detail="Persisted extraction text does not match the original uploaded document")
        metadata = dict(metadata)
        metadata["source_text_sha256"] = source_digest
        analysis.candidates = metadata
        db.add(analysis)
        db.flush()
    return content


def _response(analysis: CaptureAnalysis) -> dict:
    details = analysis.candidates if isinstance(analysis.candidates, dict) else {}
    intelligence = details.get("document_intelligence")
    intelligence = intelligence if isinstance(intelligence, dict) else {}
    observations = intelligence.get("observations")
    observations = observations if isinstance(observations, list) else []
    return {
        "analysis_id": analysis.id,
        "source_evidence_id": analysis.source_evidence_id,
        "extraction_status": analysis.extraction_status,
        "extraction_method": details.get("extraction_method"),
        "source_text_sha256": hashlib.sha256((analysis.source_text or "").strip().encode("utf-8")).hexdigest(),
        "extracted_char_count": len(analysis.source_text or ""),
        "candidate_count": len(observations),
        "intelligence_status": intelligence.get("status") or "not_run",
        "review_required": any(
            isinstance(item, dict) and item.get("review_status") in {"pending_review", "corrected_pending_approval"}
            for item in observations
        ),
        "written_to_lcr": any(
            isinstance(item, dict) and item.get("review_status") == "accepted_by_caregiver"
            for item in observations
        ),
    }


@router.post("/{evidence_id}/extract", status_code=201)
def extract_saved_document(evidence_id: str, request: Request, db: Session = Depends(get_db)):
    """Extract from the persisted original bytes and bind the result to its evidence ID."""
    person_id, caregiver_id = _identity(request, db)
    evidence = db.query(Evidence).filter(
        Evidence.id == evidence_id,
        Evidence.person_id == person_id,
        Evidence.uploaded_by_caregiver_id == caregiver_id,
        Evidence.type == "document",
    ).with_for_update().first()
    if evidence is None:
        raise HTTPException(status_code=404, detail="Saved document not found in this care record")
    upload = db.query(EvidenceUpload).filter(EvidenceUpload.evidence_id == evidence_id).first()
    if upload is None:
        raise HTTPException(status_code=404, detail="Original document bytes are unavailable")

    # Verify the immutable original on every request, including idempotent retries.
    # A cached analysis must never hide later byte corruption or source replacement.
    content = _verified_content(upload)
    existing = db.query(CaptureAnalysis).filter(
        CaptureAnalysis.person_id == person_id,
        CaptureAnalysis.caregiver_id == caregiver_id,
        CaptureAnalysis.source_evidence_id == evidence_id,
    ).order_by(CaptureAnalysis.created_at.desc()).all()
    for row in existing:
        metadata = row.candidates if isinstance(row.candidates, dict) else {}
        if metadata.get("kind") == "document_extraction_v1":
            _verify_persisted_extraction(row, upload, db, content)
            db.commit()
            db.refresh(row)
            return _response(row)

    source_text, method = _extract(upload, content)
    analysis = CaptureAnalysis(
        person_id=person_id,
        caregiver_id=caregiver_id,
        source_event_id=None,
        source_evidence_id=evidence_id,
        source_text=source_text,
        extraction_status="available",
        candidates={
            "kind": "document_extraction_v1",
            "filename": upload.filename,
            "mime_type": upload.mime_type,
            "binary_sha256": upload.sha256,
            "source_text_sha256": hashlib.sha256(source_text.strip().encode("utf-8")).hexdigest(),
            "extraction_method": method,
            "observations": [],
            "open_questions": [],
        },
        review_history=[],
    )
    db.add(analysis)
    db.commit()
    db.refresh(analysis)
    return _response(analysis)


@router.get("/analyses/{analysis_id}")
def get_document_extraction(analysis_id: str, request: Request, db: Session = Depends(get_db)):
    person_id, caregiver_id = _identity(request, db)
    analysis = db.query(CaptureAnalysis).filter(
        CaptureAnalysis.id == analysis_id,
        CaptureAnalysis.person_id == person_id,
        CaptureAnalysis.caregiver_id == caregiver_id,
    ).first()
    if analysis is None:
        raise HTTPException(status_code=404, detail="Document extraction not found")
    details = analysis.candidates if isinstance(analysis.candidates, dict) else {}
    if details.get("kind") != "document_extraction_v1":
        raise HTTPException(status_code=404, detail="Document extraction not found")
    evidence = db.query(Evidence).filter(
        Evidence.id == analysis.source_evidence_id,
        Evidence.person_id == person_id,
        Evidence.uploaded_by_caregiver_id == caregiver_id,
        Evidence.type == "document",
    ).first()
    if evidence is None:
        raise HTTPException(status_code=404, detail="Document extraction not found")
    upload = db.query(EvidenceUpload).filter(
        EvidenceUpload.evidence_id == analysis.source_evidence_id
    ).first()
    if upload is None:
        raise HTTPException(status_code=404, detail="Original document is unavailable")
    content = _verify_persisted_extraction(analysis, upload, db)
    db.commit()
    db.refresh(analysis)
    details = analysis.candidates if isinstance(analysis.candidates, dict) else {}
    intelligence = details.get("document_intelligence")
    if isinstance(intelligence, dict) and intelligence.get("source_text_sha256"):
        current_text_hash = hashlib.sha256((analysis.source_text or "").strip().encode("utf-8")).hexdigest()
        if intelligence["source_text_sha256"] != current_text_hash:
            raise HTTPException(status_code=409, detail="The persisted extraction text changed after document understanding")
    return {
        **_response(analysis),
        "extracted_text": analysis.source_text,
        "document": {
            "filename": details.get("filename"),
            "mime_type": details.get("mime_type"),
            "binary_sha256": details.get("binary_sha256"),
        },
    }
