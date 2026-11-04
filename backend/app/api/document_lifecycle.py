"""Persist extraction results against the immutable uploaded evidence artifact.

This first lifecycle stage deliberately performs deterministic text/PDF extraction only.
It does not claim OCR for scanned images or create clinical assertions.
"""
from __future__ import annotations

import hashlib
import io
from datetime import datetime, timezone

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


def _extract(upload: EvidenceUpload) -> tuple[str, str]:
    content = bytes(upload.content)
    if len(content) != upload.size_bytes or hashlib.sha256(content).hexdigest() != upload.sha256:
        raise HTTPException(status_code=500, detail="Stored evidence failed its integrity check")
    mime = (upload.mime_type or "").split(";", 1)[0].lower()
    filename = (upload.filename or "").lower()
    if mime == "application/pdf" or filename.endswith(".pdf"):
        try:
            reader = PdfReader(io.BytesIO(content), strict=True)
            if len(reader.pages) > MAX_PDF_PAGES:
                raise HTTPException(status_code=413, detail="PDFs are limited to 100 pages")
            extracted = "\n".join(page.extract_text() or "" for page in reader.pages).strip()
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=422, detail="The saved PDF could not be read") from exc
        if not extracted:
            raise HTTPException(
                status_code=422,
                detail="No embedded PDF text was found. Scanned-image OCR is not available; the original document remains stored.",
            )
        return extracted[:MAX_EXTRACTED_CHARS], "server_pdf_text"
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
        detail="Server extraction currently supports UTF-8 text and text-based PDF only. The original file remains preserved.",
    )


def _response(analysis: CaptureAnalysis) -> dict:
    details = analysis.candidates if isinstance(analysis.candidates, dict) else {}
    return {
        "analysis_id": analysis.id,
        "source_evidence_id": analysis.source_evidence_id,
        "extraction_status": analysis.extraction_status,
        "extraction_method": details.get("extraction_method"),
        "source_text_sha256": hashlib.sha256((analysis.source_text or "").encode("utf-8")).hexdigest(),
        "extracted_char_count": len(analysis.source_text or ""),
        "candidate_count": len(details.get("observations", [])),
        "review_required": True,
        "written_to_lcr": False,
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

    existing = db.query(CaptureAnalysis).filter(
        CaptureAnalysis.person_id == person_id,
        CaptureAnalysis.caregiver_id == caregiver_id,
        CaptureAnalysis.source_evidence_id == evidence_id,
    ).order_by(CaptureAnalysis.created_at.desc()).all()
    for row in existing:
        metadata = row.candidates if isinstance(row.candidates, dict) else {}
        if metadata.get("kind") == "document_extraction_v1":
            return _response(row)

    source_text, method = _extract(upload)
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
    return {
        **_response(analysis),
        "extracted_text": analysis.source_text,
        "document": {
            "filename": details.get("filename"),
            "mime_type": details.get("mime_type"),
            "binary_sha256": details.get("binary_sha256"),
        },
    }
