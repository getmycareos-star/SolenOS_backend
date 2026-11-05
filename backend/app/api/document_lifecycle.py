"""Persist extraction results against the immutable uploaded evidence artifact.

This first lifecycle stage deliberately performs deterministic text/PDF extraction only.
It does not claim OCR for scanned images or create clinical assertions.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.anonymous_session import AnonymousSession
from app.models.care import Evidence
from app.models.capture_analysis import CaptureAnalysis
from app.models.collaboration import EvidenceUpload
from app.services.document_extraction import extract_document
from app.services.qwen_gateway import extract_review_candidates

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


def _verified_content(upload: EvidenceUpload) -> bytes:
    """Verify the persisted original before both first extraction and cached retries."""
    content = bytes(upload.content)
    if len(content) != upload.size_bytes or hashlib.sha256(content).hexdigest() != upload.sha256:
        raise HTTPException(status_code=500, detail="Stored evidence failed its integrity check")
    return content


def _extract(upload: EvidenceUpload, evidence: Evidence, content: bytes | None = None) -> dict:
    verified = _verified_content(upload) if content is None else content
    try:
        evidence_metadata = json.loads(evidence.extra_metadata or "{}")
    except (TypeError, ValueError):
        evidence_metadata = {}
    scan_processing = evidence_metadata.get("scan_processing", {})
    return extract_document(
        upload.filename,
        upload.mime_type,
        verified,
        scan_processing=scan_processing if isinstance(scan_processing, dict) else None,
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
        CaptureAnalysis.source_evidence_id == evidence_id,
    ).order_by(CaptureAnalysis.created_at.desc()).all()
    for row in existing:
        metadata = row.candidates if isinstance(row.candidates, dict) else {}
        if metadata.get("kind") == "document_extraction_v1":
            if metadata.get("binary_sha256") != upload.sha256:
                raise HTTPException(
                    status_code=409,
                    detail="The stored document bytes changed after extraction; cached text cannot be reused.",
                )
            return _response(row)

    extraction = _extract(upload, evidence, content)
    source_text = extraction["text"]
    method = extraction["method"]
    page_metadata = extraction["pages"]
    candidate_extraction_status = "not_applicable"
    candidates = {"observations": [], "open_questions": []}
    if source_text:
        try:
            candidates = extract_review_candidates(
                source_text,
                source_event_id=None,
                source_evidence_id=evidence_id,
            )
            candidate_extraction_status = "available"
        except Exception:
            # Extraction remains durable if the interpretation model is unavailable.
            candidate_extraction_status = "unavailable"

    analysis = CaptureAnalysis(
        person_id=person_id,
        caregiver_id=caregiver_id,
        source_event_id=None,
        source_evidence_id=evidence_id,
        source_text=source_text,
        extraction_status="available" if source_text else "unreadable",
        candidates={
            "kind": "document_extraction_v1",
            "filename": upload.filename,
            "mime_type": upload.mime_type,
            "binary_sha256": upload.sha256,
            "extraction_method": method,
            "pages": page_metadata,
            "truncated": extraction["truncated"],
            "candidate_extraction_status": candidate_extraction_status,
            "candidate_extraction_attempts": [{
                "attempt": 1,
                "status": candidate_extraction_status,
                "attempted_by_caregiver_id": caregiver_id,
                "attempted_at": datetime.now(timezone.utc).isoformat(),
                "candidate_count": len(candidates.get("observations", [])),
            }],
            "candidate_extraction_source_chars": min(len(source_text), 12_000),
            "candidate_extraction_truncated": len(source_text) > 12_000,
            "observations": candidates.get("observations", []),
            "open_questions": candidates.get("open_questions", []),
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
    ).first()
    if analysis is None:
        raise HTTPException(status_code=404, detail="Document extraction not found")
    details = analysis.candidates if isinstance(analysis.candidates, dict) else {}
    if details.get("kind") != "document_extraction_v1":
        raise HTTPException(status_code=404, detail="Document extraction not found")
    return {
        **_response(analysis),
        "extracted_text": analysis.source_text,
        "candidates": details.get("observations", []),
        "open_questions": details.get("open_questions", []),
        "candidate_extraction_status": details.get("candidate_extraction_status", "not_applicable"),
        "candidate_extraction_source_chars": details.get("candidate_extraction_source_chars", 0),
        "candidate_extraction_attempts": details.get("candidate_extraction_attempts", []),
        "candidate_extraction_truncated": details.get("candidate_extraction_truncated", False),
        "document": {
            "filename": details.get("filename"),
            "mime_type": details.get("mime_type"),
            "binary_sha256": details.get("binary_sha256"),
            "pages": details.get("pages", []),
            "truncated": details.get("truncated", False),
        },
    }
