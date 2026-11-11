"""Source-grounded document intelligence on persisted extraction records.

The immutable upload and deterministic extraction remain authoritative source
material. Model output is review-only. Only an explicit caregiver acceptance
creates a source-linked CareAssertion.
"""
from __future__ import annotations

import copy
import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.api.document_lifecycle import _verify_persisted_extraction
from app.api.care_continuity import reconcile_person_change_intelligence
from app.models.anonymous_session import AnonymousSession
from app.models.care import Evidence
from app.models.care_continuity import CareAssertion
from app.models.capture_analysis import CaptureAnalysis, DocumentReviewEvent
from app.schemas.care_continuity import CareAssertionCreate
from app.services.care_continuity import create_assertion
from app.models.collaboration import EvidenceUpload
from app.services.qwen_gateway import extract_review_candidates
from app.services.document_candidate_schema import canonical_candidate, split_document_pages, apply_medication_conflict_warnings

router = APIRouter(prefix="/documents", tags=["document intelligence"])
logger = logging.getLogger(__name__)
MAX_DOCUMENT_QWEN_PAGES = 25


class CandidateReview(BaseModel):
    candidate_index: int = Field(ge=0)
    decision: Literal["accept", "correct", "reject"]
    corrected_subject: str | None = Field(default=None, max_length=120)
    corrected_observation: str | None = Field(default=None, max_length=500)
    rationale: str | None = Field(default=None, max_length=1000)


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


def _metadata(analysis: CaptureAnalysis) -> dict:
    value = analysis.candidates if isinstance(analysis.candidates, dict) else {}
    if value.get("kind") != "document_extraction_v1":
        raise HTTPException(status_code=409, detail="A persisted server-side document extraction is required first")
    return value


def _source(evidence_id: str, person_id: str, caregiver_id: str, db: Session):
    evidence = db.query(Evidence).filter(
        Evidence.id == evidence_id,
        Evidence.person_id == person_id,
        Evidence.uploaded_by_caregiver_id == caregiver_id,
        Evidence.type == "document",
    ).with_for_update().first()
    if evidence is None:
        raise HTTPException(status_code=404, detail="Saved document not found in this caregiver's care record")
    upload = db.query(EvidenceUpload).filter(EvidenceUpload.evidence_id == evidence_id).first()
    if upload is None:
        raise HTTPException(status_code=409, detail="The original uploaded document is unavailable")
    content = bytes(upload.content)
    digest = hashlib.sha256(content).hexdigest()
    if len(content) != upload.size_bytes or digest != upload.sha256:
        raise HTTPException(status_code=500, detail="Stored evidence failed its integrity check")
    return evidence, upload


def _analysis_for_evidence(evidence_id: str, person_id: str, caregiver_id: str, db: Session):
    rows = db.query(CaptureAnalysis).filter(
        CaptureAnalysis.person_id == person_id,
        CaptureAnalysis.caregiver_id == caregiver_id,
        CaptureAnalysis.source_evidence_id == evidence_id,
    ).order_by(CaptureAnalysis.created_at.desc()).with_for_update().all()
    for row in rows:
        if isinstance(row.candidates, dict) and row.candidates.get("kind") == "document_extraction_v1":
            return row
    raise HTTPException(status_code=409, detail="Run server-side extraction before document understanding")


def _intelligence(analysis: CaptureAnalysis) -> dict:
    metadata = _metadata(analysis)
    intelligence = metadata.get("document_intelligence")
    return intelligence if isinstance(intelligence, dict) else {}


def _response(analysis: CaptureAnalysis) -> dict:
    metadata = _metadata(analysis)
    intelligence = _intelligence(analysis)
    observations = intelligence.get("observations") if isinstance(intelligence.get("observations"), list) else []
    return {
        "analysis_id": str(analysis.id),
        "source_evidence_id": str(analysis.source_evidence_id),
        "document_name": metadata.get("filename") or "Uploaded document",
        "document_type": metadata.get("document_type") or "general_care_document",
        "page_count": metadata.get("page_count"),
        "extraction_status": analysis.extraction_status,
        "intelligence_status": intelligence.get("status") or "not_run",
        "extraction_method": metadata.get("extraction_method"),
        "extracted_char_count": len(analysis.source_text or ""),
        "observations": observations,
        "open_questions": intelligence.get("open_questions", []),
        "review_required": any(
            isinstance(item, dict) and item.get("review_status") in {"pending_review", "corrected_pending_approval"}
            for item in observations
        ),
        "written_to_lcr": any(
            isinstance(item, dict) and item.get("review_status") == "accepted_by_caregiver"
            for item in observations
        ),
    }


@router.get("/intelligence")
def list_document_intelligence(
    request: Request,
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    person_id, caregiver_id = _identity(request, db)
    rows = db.query(CaptureAnalysis).filter(
        CaptureAnalysis.person_id == person_id,
        CaptureAnalysis.caregiver_id == caregiver_id,
    ).order_by(CaptureAnalysis.created_at.desc()).limit(500).all()
    items = []
    for analysis in rows:
        metadata = analysis.candidates if isinstance(analysis.candidates, dict) else {}
        if metadata.get("kind") != "document_extraction_v1":
            continue
        evidence = db.query(Evidence.id).filter(
            Evidence.id == analysis.source_evidence_id,
            Evidence.person_id == person_id,
            Evidence.uploaded_by_caregiver_id == caregiver_id,
            Evidence.type == "document",
        ).first()
        if evidence is None:
            continue
        items.append(_response(analysis))
        if len(items) >= limit:
            break
    return {"items": items, "count": len(items), "identity_scope": "server_session_person_and_caregiver"}


@router.post("/{evidence_id}/intelligence", status_code=201)
def analyze_saved_document(evidence_id: str, request: Request, db: Session = Depends(get_db)):
    person_id, caregiver_id = _identity(request, db)
    evidence, upload = _source(evidence_id, person_id, caregiver_id, db)
    analysis = _analysis_for_evidence(evidence_id, person_id, caregiver_id, db)
    metadata = _metadata(analysis)
    if metadata.get("binary_sha256") != upload.sha256:
        raise HTTPException(status_code=409, detail="The extraction does not match the preserved original file")
    _verify_persisted_extraction(analysis, upload, db)
    metadata = _metadata(analysis)
    existing = _intelligence(analysis)
    if existing:
        return _response(analysis)

    source_text = (analysis.source_text or "").strip()
    if not source_text:
        raise HTTPException(status_code=422, detail="No persisted extracted text is available; no document interpretation was generated")
    # Process each page independently so a long discharge packet cannot be
    # silently truncated to the first 12k characters by the Qwen gateway.
    raw_observations = []
    all_open_questions = []
    page_inputs = split_document_pages(source_text)
    if not page_inputs:
        raise HTTPException(status_code=422, detail="No readable page text is available for document understanding")
    for page_number, page_text in page_inputs[:MAX_DOCUMENT_QWEN_PAGES]:
        try:
            page_result = extract_review_candidates(
                page_text,
                source_event_id="",
                source_evidence_id=str(evidence.id),
            )
        except HTTPException:
            db.rollback()
            raise
        if isinstance(page_result, dict):
            page_items = page_result.get("observations")
            if isinstance(page_items, list):
                raw_observations.extend(page_items)
            page_questions = page_result.get("open_questions")
            if isinstance(page_questions, list):
                all_open_questions.extend(
                    value.strip()[:300]
                    for value in page_questions
                    if isinstance(value, str) and value.strip()
                )
    candidate_overflow = len(raw_observations) > 60
    raw_observations = raw_observations[:60]
    grounded = []
    seen_quotes = set()
    for item in raw_observations:
        if not isinstance(item, dict):
            continue
        quote = item.get("source_quote")
        subject = item.get("subject")
        observation = item.get("observation")
        if not all(isinstance(value, str) and value.strip() for value in (quote, subject, observation)):
            continue
        if quote not in source_text or quote in seen_quotes:
            continue
        # The model may identify the quote, but it may not paraphrase or extend it.
        if observation.strip() not in quote or subject.strip().casefold() not in quote.casefold():
            continue
        seen_quotes.add(quote)
        canonical = canonical_candidate(
            item,
            document_id=str(evidence.id),
            source_text=source_text,
        )
        extraction_method = str(metadata.get("extraction_method") or "")
        if "server_pdf_text" in extraction_method:
            canonical["warnings"].append("PDF text extraction may flatten multi-column or table reading order; compare this passage with the original page.")
        if "tesseract" in extraction_method:
            canonical["warnings"].append("OCR text may misread names, dates, decimal points, strengths or dosage instructions; verify against the original page.")
        grounded.append({
            "subject": subject.strip()[:120],
            "observation": observation.strip()[:500],
            "source_quote": quote,
            "source_evidence_id": str(evidence.id),
            "review_status": "pending_review",
            "extraction_method": str(item.get("extraction_method") or "qwen_verbatim_source_extraction"),
            "page_number": canonical["page_number"],
            "source_excerpt": canonical["source_excerpt"],
            "field_type": canonical["field_type"],
            "extracted_value": canonical["extracted_value"],
            "event_date": canonical["event_date"],
            "recorded_date": canonical["recorded_date"],
            "confidence": canonical["confidence"],
            "warnings": canonical["warnings"],
            "canonical_candidate": canonical,
        })
    apply_medication_conflict_warnings(grounded)
    for candidate in grounded:
        canonical = candidate["canonical_candidate"]
        canonical["warnings"] = list(candidate.get("warnings") or canonical.get("warnings") or [])


    metadata = copy.deepcopy(metadata)
    metadata["document_intelligence"] = {
        "version": 1,
        "observations": grounded,
        "open_questions": [
            *[
                value.strip()[:300]
                for value in all_open_questions[:3]
                if isinstance(value, str) and value.strip()
            ],
            *(
                [f"Qwen processed {MAX_DOCUMENT_QWEN_PAGES} of {len(page_inputs)} readable pages. Remaining pages need a separate extraction pass."]
                if len(page_inputs) > MAX_DOCUMENT_QWEN_PAGES else []
            ),
            *(
                ["The candidate review limit was reached; additional extracted candidates were not included and require a follow-up extraction."]
                if candidate_overflow else []
            ),
        ],
        "processed_page_count": min(len(page_inputs), MAX_DOCUMENT_QWEN_PAGES),
        "total_readable_page_count": len(page_inputs),
        "status": "partial" if len(page_inputs) > MAX_DOCUMENT_QWEN_PAGES else "available",
        "model_authority": "review_only",
        "source_text_sha256": hashlib.sha256(source_text.encode("utf-8")).hexdigest(),
    }
    analysis.candidates = metadata
    db.add(analysis)
    db.commit()
    db.refresh(analysis)
    return _response(analysis)





@router.get("/intelligence/{analysis_id}/review-history")
def list_document_review_history(
    analysis_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    person_id, caregiver_id = _identity(request, db)
    analysis = db.query(CaptureAnalysis.id).filter(
        CaptureAnalysis.id == analysis_id,
        CaptureAnalysis.person_id == person_id,
        CaptureAnalysis.caregiver_id == caregiver_id,
    ).first()
    if analysis is None:
        raise HTTPException(status_code=404, detail="Document analysis not found")
    rows = db.query(DocumentReviewEvent).filter(
        DocumentReviewEvent.analysis_id == analysis_id,
        DocumentReviewEvent.person_id == person_id,
        DocumentReviewEvent.caregiver_id == caregiver_id,
    ).order_by(
        DocumentReviewEvent.candidate_index.asc(),
        DocumentReviewEvent.decision_version.asc(),
    ).all()
    return {
        "analysis_id": analysis_id,
        "items": [
            {
                "id": row.id,
                "candidate_index": row.candidate_index,
                "decision_version": row.decision_version,
                "decision": row.decision,
                "rationale": row.rationale,
                "before": row.before_state,
                "after": row.after_state,
                "source_evidence_id": row.source_evidence_id,
                "resulting_assertion_id": row.resulting_assertion_id,
                "reviewed_by_caregiver_id": row.caregiver_id,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ],
        "count": len(rows),
        "history_authority": "append_only_document_review_events",
    }


@router.post("/intelligence/{analysis_id}/review")
def review_document_candidate(
    analysis_id: str,
    payload: CandidateReview,
    request: Request,
    db: Session = Depends(get_db),
):
    person_id, caregiver_id = _identity(request, db)
    analysis = db.query(CaptureAnalysis).filter(
        CaptureAnalysis.id == analysis_id,
        CaptureAnalysis.person_id == person_id,
        CaptureAnalysis.caregiver_id == caregiver_id,
    ).with_for_update().first()
    if analysis is None:
        raise HTTPException(status_code=404, detail="Document analysis not found")
    metadata = _metadata(analysis)
    intelligence = _intelligence(analysis)
    observations = intelligence.get("observations")
    if not isinstance(observations, list) or payload.candidate_index >= len(observations):
        raise HTTPException(status_code=404, detail="Document candidate not found")
    evidence_id = str(analysis.source_evidence_id or "")
    evidence, upload = _source(evidence_id, person_id, caregiver_id, db)
    if metadata.get("binary_sha256") != upload.sha256:
        raise HTTPException(status_code=409, detail="The original document no longer matches the extracted source")
    _verify_persisted_extraction(analysis, upload, db)
    metadata = _metadata(analysis)
    intelligence = _intelligence(analysis)
    observations = intelligence.get("observations")
    if not isinstance(observations, list) or payload.candidate_index >= len(observations):
        raise HTTPException(status_code=404, detail="Document candidate not found")
    if hashlib.sha256((analysis.source_text or "").strip().encode("utf-8")).hexdigest() != intelligence.get("source_text_sha256"):
        raise HTTPException(status_code=409, detail="The persisted extraction text changed after candidate generation")

    updated = copy.deepcopy(metadata)
    intelligence = updated["document_intelligence"]
    candidate = intelligence["observations"][payload.candidate_index]
    status = candidate.get("review_status")
    if status in {"accepted_by_caregiver", "rejected_by_caregiver"}:
        prior = candidate.get("review_decision")
        if prior == payload.decision:
            accepted_assertion = None
            if prior == "accept" and candidate.get("accepted_assertion_id"):
                accepted_assertion = db.query(CareAssertion).filter(
                    CareAssertion.id == candidate.get("accepted_assertion_id"),
                    CareAssertion.person_id == person_id,
                    CareAssertion.created_by_caregiver_id == caregiver_id,
                ).first()
            active_assertion = (
                accepted_assertion is not None
                and accepted_assertion.assertion_status not in {"rejected", "superseded"}
            )
            return {
                "analysis_id": analysis_id,
                "candidate": candidate,
                "review_decision": prior,
                "source_record_ref": (
                    {"record_type": "care_assertion", "record_id": accepted_assertion.id}
                    if active_assertion else None
                ),
                "written_to_lcr": bool(active_assertion),
                "review_required": False,
                "idempotent_replay": True,
            }
        raise HTTPException(status_code=409, detail="This candidate already has a final caregiver decision")
    if status == "corrected_pending_approval" and payload.decision == "correct":
        requested_subject = (
            payload.corrected_subject if payload.corrected_subject is not None
            else candidate.get("subject", "")
        ).strip()
        requested_observation = (
            payload.corrected_observation if payload.corrected_observation is not None
            else candidate.get("observation", "")
        ).strip()
        if (
            requested_subject == candidate.get("subject")
            and requested_observation == candidate.get("observation")
        ):
            prior_event = db.query(DocumentReviewEvent).filter(
                DocumentReviewEvent.analysis_id == analysis_id,
                DocumentReviewEvent.candidate_index == payload.candidate_index,
            ).order_by(DocumentReviewEvent.decision_version.desc()).first()
            return {
                "analysis_id": analysis_id,
                "candidate": candidate,
                "review_decision": "correct",
                "review_event_id": prior_event.id if prior_event else None,
                "source_record_ref": None,
                "written_to_lcr": False,
                "review_required": True,
                "idempotent_replay": True,
            }
    if status not in {"pending_review", "corrected_pending_approval"}:
        raise HTTPException(status_code=409, detail="This candidate is not awaiting caregiver review")

    original = dict(candidate)
    assertion_id = None
    if payload.decision == "reject":
        candidate["review_status"] = "rejected_by_caregiver"
    elif payload.decision == "correct":
        subject = (payload.corrected_subject if payload.corrected_subject is not None else candidate.get("subject", "")).strip()
        observation = (payload.corrected_observation if payload.corrected_observation is not None else candidate.get("observation", "")).strip()
        if not subject or not observation:
            raise HTTPException(status_code=422, detail="A subject and corrected observation are required")
        candidate["subject"] = subject[:120]
        candidate["observation"] = observation[:500]
        candidate["review_status"] = "corrected_pending_approval"
    else:
        quote = candidate.get("source_quote")
        if not isinstance(quote, str) or not quote or quote not in (analysis.source_text or ""):
            raise HTTPException(status_code=409, detail="The candidate quotation is not present in the preserved extraction")
        subject = str(candidate.get("subject") or "").strip()
        observation = str(candidate.get("observation") or "").strip()
        if not subject or not observation:
            raise HTTPException(status_code=422, detail="The candidate is incomplete and cannot be accepted")
        asserted_value = f"Subject: {subject}\nObservation: {observation}"
        source_label = f"Caregiver-reviewed document statement (not independently verified): {metadata.get('filename') or 'Uploaded document'}"
        # Recover an assertion if a prior request committed the assertion but
        # failed before the candidate review state was committed.
        existing = db.query(CareAssertion).filter(
            CareAssertion.person_id == person_id,
            CareAssertion.field == "document_extracted_observation",
            CareAssertion.assertion_kind == "document_reviewed_observation",
            CareAssertion.source_text == quote,
            CareAssertion.created_by_caregiver_id == caregiver_id,
        ).order_by(CareAssertion.recorded_at.desc()).all()
        assertion_row = next(
            (row for row in existing if evidence_id in (row.evidence_ids or []) and row.asserted_value == asserted_value),
            None,
        )
        # Use ORM writes on this path so the CareStateRevision listener and
        # the document review event share the same database transaction. The
        # generic care-continuity helper commits independently in other routes;
        # document review must not expose an assertion without its audit event.
        if assertion_row is None:
            assertion_row = create_assertion(
                db,
                CareAssertionCreate(
                    person_id=person_id,
                    subject_type="person",
                    subject_id=person_id,
                    field="document_extracted_observation",
                    assertion_kind="document_reviewed_observation",
                    asserted_value=asserted_value,
                    source_label=source_label,
                    source_text=quote,
                    evidence_ids=[evidence_id],
                    created_by_caregiver_id=caregiver_id,
                    updated_by_caregiver_id=caregiver_id,
                ),
                commit=False,
            )
            assertion_row.assertion_status = "accepted"
            db.flush()
        else:
            assertion_row = db.query(CareAssertion).filter(
                CareAssertion.id == assertion_row.id,
                CareAssertion.person_id == person_id,
                CareAssertion.created_by_caregiver_id == caregiver_id,
            ).with_for_update().first()
            if assertion_row is None:
                raise HTTPException(status_code=404, detail="Source-linked assertion not found in this care record")

        assertion_id = str(assertion_row.id)
        # Flush the assertion and its state revision in the same transaction as
        # the append-only review event below. No intermediate commit is allowed.
        db.flush()
        if assertion_row.assertion_status != "accepted":
            raise HTTPException(status_code=503, detail="The caregiver review was not applied to the Living Care Record")

        candidate["review_status"] = "accepted_by_caregiver"
        candidate["accepted_assertion_id"] = assertion_id
        candidate["accepted_at"] = datetime.now(timezone.utc).isoformat()

    canonical = candidate.get("canonical_candidate")
    if isinstance(canonical, dict):
        canonical["review_status"] = {
            "accept": "Accepted",
            "correct": "Corrected",
            "reject": "Rejected",
        }[payload.decision]
        canonical["extracted_value"] = candidate.get("observation") or None

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
        "before": original,
        "after": dict(candidate),
        "source_evidence_id": evidence_id,
    })
    # The append-only event table is the audit source of truth. The JSON
    # history remains a compatibility projection for older clients only.
    decision_version = db.query(DocumentReviewEvent).filter(
        DocumentReviewEvent.analysis_id == analysis_id,
        DocumentReviewEvent.candidate_index == payload.candidate_index,
    ).count() + 1
    db.add(DocumentReviewEvent(
        analysis_id=analysis_id,
        person_id=person_id,
        caregiver_id=caregiver_id,
        source_evidence_id=evidence_id,
        candidate_index=payload.candidate_index,
        decision_version=decision_version,
        decision=payload.decision,
        rationale=payload.rationale,
        before_state=original,
        after_state=dict(candidate),
        resulting_assertion_id=assertion_id,
    ))
    analysis.candidates = updated
    analysis.review_history = history
    db.add(analysis)
    db.commit()
    db.refresh(analysis)
    change_intelligence_status = None
    if payload.decision == "accept":
        try:
            reconcile_person_change_intelligence(db, person_id)
            db.commit()
            change_intelligence_status = "available"
        except Exception:
            db.rollback()
            logger.exception(
                "Change intelligence reconciliation failed after document assertion review %s",
                assertion_id,
            )
            change_intelligence_status = "unavailable"
    return {
        "analysis_id": analysis_id,
        "candidate": candidate,
        "review_decision": payload.decision,
        "source_evidence_id": evidence_id,
        "source_record_ref": {"record_type": "care_assertion", "record_id": assertion_id} if assertion_id else None,
        "written_to_lcr": bool(assertion_id),
        "review_required": payload.decision == "correct",
        "change_intelligence_status": change_intelligence_status,
    }
