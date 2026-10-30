"""Document intelligence extraction and caregiver-reviewed LCR promotion.

Original uploads remain immutable Evidence. Qwen produces verbatim-grounded
review candidates; only an explicit caregiver acceptance creates a source-linked
CareAssertion in the Living Care Record.
"""
from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.anonymous_session import AnonymousSession
from app.models.care import Evidence
from app.models.care_continuity import CareAssertion
from app.models.capture_analysis import CaptureAnalysis
from app.services.qwen_gateway import extract_review_candidates

router = APIRouter(prefix="/documents", tags=["document intelligence"])


class DocumentCandidateReview(BaseModel):
    candidate_index: int = Field(ge=0)
    decision: Literal["accept", "correct", "reject"]
    corrected_subject: str | None = Field(default=None, max_length=120)
    corrected_observation: str | None = Field(default=None, max_length=500)


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


def _document_name(evidence: Evidence) -> str:
    metadata = evidence.extra_metadata or {}
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except (TypeError, ValueError):
            metadata = {}
    if not isinstance(metadata, dict):
        metadata = {}
    value = metadata.get("filename")
    return str(value).strip()[:255] if value else "Uploaded document"


def _analysis_response(analysis: CaptureAnalysis) -> dict:
    candidates = analysis.candidates if isinstance(analysis.candidates, dict) else {}
    return {
        "status": analysis.extraction_status,
        "analysis_id": str(analysis.id),
        "source_evidence_id": str(analysis.source_evidence_id) if analysis.source_evidence_id else None,
        "document_intelligence": candidates,
        "review_required": True,
        "written_to_lcr": False,
        "notice": "Extracted statements are source-grounded review candidates, not verified clinical facts. Only caregiver acceptance promotes a source-linked assertion into the Living Care Record.",
    }


@router.get("/intelligence")
def list_document_intelligence(
    request: Request,
    limit: int = Query(default=30, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """Return persisted document analyses for this authenticated caregiver and person."""
    person_id, caregiver_id = _identity(request, db)
    rows = db.query(CaptureAnalysis).filter(
        CaptureAnalysis.person_id == person_id,
        CaptureAnalysis.caregiver_id == caregiver_id,
    ).order_by(CaptureAnalysis.created_at.desc()).limit(min(limit * 5, 500)).all()
    results = [
        _analysis_response(row)
        for row in rows
        if isinstance(row.candidates, dict) and row.candidates.get("kind") == "document_intelligence_v1"
    ][:limit]
    return {"items": results, "count": len(results), "identity_scope": "server_session_person_and_caregiver"}


@router.get("/intelligence")
def list_document_intelligence(
    request: Request,
    limit: int = 50,
    db: Session = Depends(get_db),
):
    """List only this caregiver's persisted document analyses for library review."""
    person_id, caregiver_id = _identity(request, db)
    bounded_limit = max(1, min(int(limit), 100))
    rows = (
        db.query(CaptureAnalysis)
        .filter(
            CaptureAnalysis.person_id == person_id,
            CaptureAnalysis.caregiver_id == caregiver_id,
        )
        .order_by(CaptureAnalysis.created_at.desc())
        .limit(bounded_limit)
        .all()
    )
    results = []
    for analysis in rows:
        candidates = analysis.candidates if isinstance(analysis.candidates, dict) else {}
        if candidates.get("kind") != "document_intelligence_v1":
            continue
        evidence = db.query(Evidence).filter(
            Evidence.id == analysis.source_evidence_id,
            Evidence.person_id == person_id,
            Evidence.uploaded_by_caregiver_id == caregiver_id,
            Evidence.type == "document",
        ).first()
        if evidence is None:
            continue
        results.append(_analysis_response(analysis))
    return {"items": results, "count": len(results), "limit": bounded_limit}


@router.post("/{evidence_id}/intelligence", status_code=201)
def analyze_saved_document(
    evidence_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Extract verbatim-grounded candidate observations from one saved document."""
    person_id, caregiver_id = _identity(request, db)
    # Lock the source row so concurrent retries serialize before checking for an
    # existing analysis. The original evidence bytes/text are never modified.
    evidence = db.query(Evidence).filter(
        Evidence.id == evidence_id,
        Evidence.person_id == person_id,
        Evidence.uploaded_by_caregiver_id == caregiver_id,
        Evidence.type == "document",
    ).with_for_update().first()
    if evidence is None:
        raise HTTPException(status_code=404, detail="Saved document not found in this care record")
    source_text = (evidence.source_text or "").strip()
    if not source_text:
        raise HTTPException(
            status_code=422,
            detail="This saved document has no readable extracted text. The original remains stored; no intelligence was inferred from the file image.",
        )

    existing = db.query(CaptureAnalysis).filter(
        CaptureAnalysis.person_id == person_id,
        CaptureAnalysis.caregiver_id == caregiver_id,
        CaptureAnalysis.source_evidence_id == evidence_id,
    ).order_by(CaptureAnalysis.created_at.desc()).first()
    if existing is not None and isinstance(existing.candidates, dict) and existing.candidates.get("kind") == "document_intelligence_v1":
        return _analysis_response(existing)

    try:
        extracted = extract_review_candidates(
            source_text,
            source_event_id="",
            source_evidence_id=str(evidence.id),
        )
    except HTTPException:
        db.rollback()
        raise

    observations = extracted.get("observations") if isinstance(extracted, dict) else []
    if not isinstance(observations, list):
        observations = []
    # Retain only candidates whose exact source quote still exists in the saved
    # evidence text. The gateway already performs this check; repeat it here at
    # the persistence boundary.
    grounded = []
    for item in observations[:12]:
        if not isinstance(item, dict):
            continue
        quote = item.get("source_quote")
        subject = item.get("subject")
        observation = item.get("observation")
        if not all(isinstance(value, str) and value.strip() for value in (quote, subject, observation)):
            continue
        if quote not in source_text:
            continue
        grounded.append({
            "subject": subject.strip()[:120],
            "observation": observation.strip()[:500],
            "source_quote": quote,
            "source_evidence_id": str(evidence.id),
            "review_status": "pending_review",
            "extraction_method": str(item.get("extraction_method") or "qwen_verbatim_source_extraction"),
        })

    candidates = {
        "kind": "document_intelligence_v1",
        "document_name": _document_name(evidence),
        "observations": grounded,
        "open_questions": [
            str(value).strip()[:300]
            for value in (extracted.get("open_questions") or [])[:3]
            if isinstance(value, str) and value.strip()
        ],
        "extraction_method": "qwen_with_deterministic_verbatim_source_fallback",
    }
    analysis = CaptureAnalysis(
        person_id=person_id,
        caregiver_id=caregiver_id,
        source_event_id=None,
        source_evidence_id=str(evidence.id),
        source_text=source_text[:100000],
        extraction_status="available",
        candidates=candidates,
        review_history=[],
    )
    db.add(analysis)
    try:
        db.commit()
        db.refresh(analysis)
    except Exception:
        db.rollback()
        # A concurrent request may have committed the same source analysis.
        existing = db.query(CaptureAnalysis).filter(
            CaptureAnalysis.person_id == person_id,
            CaptureAnalysis.caregiver_id == caregiver_id,
            CaptureAnalysis.source_evidence_id == evidence_id,
        ).order_by(CaptureAnalysis.created_at.desc()).first()
        if existing is not None and isinstance(existing.candidates, dict) and existing.candidates.get("kind") == "document_intelligence_v1":
            return _analysis_response(existing)
        raise
    return _analysis_response(analysis)


@router.post("/intelligence/{analysis_id}/review")
def review_document_candidate(
    analysis_id: str,
    payload: DocumentCandidateReview,
    request: Request,
    db: Session = Depends(get_db),
):
    """Record a caregiver review; accepted statements become attributed LCR assertions."""
    person_id, caregiver_id = _identity(request, db)
    analysis = db.query(CaptureAnalysis).filter(
        CaptureAnalysis.id == analysis_id,
        CaptureAnalysis.person_id == person_id,
        CaptureAnalysis.caregiver_id == caregiver_id,
    ).with_for_update().first()
    if analysis is None or not isinstance(analysis.candidates, dict) or analysis.candidates.get("kind") != "document_intelligence_v1":
        raise HTTPException(status_code=404, detail="Document intelligence review not found")
    evidence_id = str(analysis.source_evidence_id or "")
    evidence = db.query(Evidence).filter(
        Evidence.id == evidence_id,
        Evidence.person_id == person_id,
        Evidence.uploaded_by_caregiver_id == caregiver_id,
        Evidence.type == "document",
    ).with_for_update().first()
    if evidence is None:
        raise HTTPException(status_code=409, detail="The original source document is no longer available in this care record")

    candidates_payload = copy.deepcopy(analysis.candidates)
    observations = candidates_payload.get("observations")
    if not isinstance(observations, list) or payload.candidate_index >= len(observations):
        raise HTTPException(status_code=404, detail="Document candidate not found")
    candidate = observations[payload.candidate_index]
    if not isinstance(candidate, dict) or candidate.get("review_status") not in {"pending_review", "corrected_pending_approval"}:
        raise HTTPException(status_code=409, detail="This document candidate already has a review decision")

    original = dict(candidate)
    if payload.decision == "reject":
        candidate["review_status"] = "rejected_by_caregiver"
    elif payload.decision == "correct":
        subject = (payload.corrected_subject if payload.corrected_subject is not None else candidate.get("subject", "")).strip()
        observation = (payload.corrected_observation if payload.corrected_observation is not None else candidate.get("observation", "")).strip()
        if not subject or not observation:
            raise HTTPException(status_code=422, detail="A subject and observation are required for a correction")
        candidate["subject"] = subject[:120]
        candidate["observation"] = observation[:500]
        candidate["review_status"] = "corrected_pending_approval"
    else:
        if candidate.get("review_status") not in {"pending_review", "corrected_pending_approval"}:
            raise HTTPException(status_code=409, detail="This document candidate is not awaiting acceptance")
        quote = candidate.get("source_quote")
        if not isinstance(quote, str) or not quote or quote not in (evidence.source_text or ""):
            raise HTTPException(status_code=409, detail="The candidate no longer matches the preserved source document")
        subject = str(candidate.get("subject") or "").strip()
        observation = str(candidate.get("observation") or "").strip()
        if not subject or not observation:
            raise HTTPException(status_code=422, detail="The candidate is incomplete and cannot be accepted")
        assertion = CareAssertion(
            person_id=person_id,
            subject_type="person",
            subject_id=person_id,
            field="document_extracted_observation",
            assertion_kind="caregiver_reviewed_document_extraction",
            asserted_value=f"Subject: {subject}\nObservation: {observation}",
            source_event_id=None,
            source_label=f"Document: {_document_name(evidence)}",
            source_text=json.dumps({
                "source_evidence_id": evidence_id,
                "source_quote": quote,
                "document_name": _document_name(evidence),
                "analysis_id": analysis_id,
                "candidate_index": payload.candidate_index,
                "interpretation_limit": "Caregiver reviewed the extracted statement and its source passage. This remains a source-attributed assertion, not an independently verified clinical fact.",
            }, ensure_ascii=False),
            evidence_ids=[evidence_id],
            assertion_status="caregiver_reviewed",
            created_by_caregiver_id=caregiver_id,
            updated_by_caregiver_id=caregiver_id,
        )
        db.add(assertion)
        db.flush()
        candidate["review_status"] = "accepted_by_caregiver"
        candidate["accepted_assertion_id"] = str(assertion.id)
        candidate["accepted_at"] = datetime.now(timezone.utc).isoformat()

    reviewed_at = datetime.now(timezone.utc).isoformat()
    candidate["reviewed_at"] = reviewed_at
    candidate["reviewed_by_caregiver_id"] = caregiver_id
    candidate["review_decision"] = payload.decision
    history = list(analysis.review_history or [])
    history.append({
        "candidate_index": payload.candidate_index,
        "decision": payload.decision,
        "reviewed_at": reviewed_at,
        "reviewed_by_caregiver_id": caregiver_id,
        "original_candidate": original,
        "reviewed_candidate": dict(candidate),
        "source_evidence_id": evidence_id,
    })
    analysis.candidates = candidates_payload
    analysis.review_history = history
    db.add(analysis)
    db.commit()
    db.refresh(analysis)

    accepted = payload.decision == "accept"
    return {
        "analysis_id": analysis_id,
        "candidate": candidate,
        "review_decision": payload.decision,
        "source_evidence_id": evidence_id,
        "source_record_ref": (
            {"record_type": "care_assertion", "record_id": candidate["accepted_assertion_id"]}
            if accepted else None
        ),
        "review_required": True,
        "written_to_lcr": accepted,
        "message": (
            "Caregiver-reviewed, source-linked assertion was saved to the Living Care Record."
            if accepted else
            "Review decision saved. The original document remains preserved; no care assertion was created."
        ),
    }
