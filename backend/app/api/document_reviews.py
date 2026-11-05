"""Auditable caregiver decisions for document-extracted candidate observations."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
import hashlib
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.api.document_lifecycle import _identity
from app.models.capture_analysis import CaptureAnalysis
from app.models.care import Evidence
from app.models.care_continuity import CareAssertion
from app.models.uncertainty import OpenQuestion
from app.models.document_review import DocumentCandidateReview
from app.services.qwen_gateway import extract_review_candidates

router = APIRouter(prefix="/documents", tags=["document review"])


class DocumentCandidateDecision(BaseModel):
    candidate_index: int = Field(ge=0)
    decision: Literal["accept", "correct", "reject", "clarify"]
    corrected_subject: str | None = Field(default=None, max_length=160)
    corrected_observation: str | None = Field(default=None, max_length=2000)
    rationale: str | None = Field(default=None, max_length=1000)
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=128)

    @model_validator(mode="after")
    def validate_correction(self):
        if self.decision == "correct" and not (
            (self.corrected_subject or "").strip()
            or (self.corrected_observation or "").strip()
        ):
            raise ValueError("A correction must include a corrected subject or observation.")
        return self


def _decision_response(row: DocumentCandidateReview) -> dict:
    return {
        "review_id": row.id,
        "analysis_id": row.analysis_id,
        "candidate_index": row.candidate_index,
        "decision_version": row.decision_version,
        "decision": row.decision,
        "source_quote": row.source_quote,
        "corrected_subject": row.corrected_subject,
        "corrected_observation": row.corrected_observation,
        "resulting_assertion_id": row.resulting_assertion_id,
        "superseded_assertion_id": row.superseded_assertion_id,
        "resulting_question_id": row.resulting_question_id,
        "created_at": row.created_at,
        "written_to_lcr": row.resulting_assertion_id is not None,
    }


@router.post("/analyses/{analysis_id}/candidates/review")
def review_document_candidate(
    analysis_id: str,
    payload: DocumentCandidateDecision,
    request: Request,
    db: Session = Depends(get_db),
):
    person_id, caregiver_id = _identity(request, db)
    if payload.idempotency_key:
        prior = db.query(DocumentCandidateReview).filter(
            DocumentCandidateReview.person_id == person_id,
            DocumentCandidateReview.idempotency_key == payload.idempotency_key,
        ).first()
        if prior is not None:
            same_request = (
                prior.analysis_id == analysis_id
                and prior.candidate_index == payload.candidate_index
                and prior.decision == payload.decision
                and prior.corrected_subject == payload.corrected_subject
                and prior.corrected_observation == payload.corrected_observation
                and prior.rationale == payload.rationale
            )
            if not same_request:
                raise HTTPException(status_code=409, detail="Idempotency key was reused with a different review decision")
            return _decision_response(prior)

    analysis = db.query(CaptureAnalysis).filter(
        CaptureAnalysis.id == analysis_id,
        CaptureAnalysis.person_id == person_id,
    ).with_for_update().first()
    if analysis is None:
        raise HTTPException(status_code=404, detail="Document extraction not found")
    metadata = analysis.candidates if isinstance(analysis.candidates, dict) else {}
    if metadata.get("kind") != "document_extraction_v1":
        raise HTTPException(status_code=404, detail="Document extraction not found")
    observations = metadata.get("observations")
    if not isinstance(observations, list) or payload.candidate_index >= len(observations):
        raise HTTPException(status_code=404, detail="Document candidate not found")
    candidate = observations[payload.candidate_index]
    if not isinstance(candidate, dict):
        raise HTTPException(status_code=422, detail="Document candidate data is invalid")

    latest = db.query(DocumentCandidateReview).filter(
        DocumentCandidateReview.analysis_id == analysis_id,
        DocumentCandidateReview.candidate_index == payload.candidate_index,
    ).order_by(DocumentCandidateReview.decision_version.desc()).first()
    if latest and latest.decision == "reject":
        raise HTTPException(status_code=409, detail="A rejected candidate is closed; create a new extraction to review changed source material")
    if latest and latest.decision == "accept" and payload.decision != "correct":
        raise HTTPException(status_code=409, detail="An accepted candidate can only be changed through a new correction decision")
    if analysis.source_evidence_id is None:
        raise HTTPException(status_code=409, detail="The extraction is not linked to original evidence")
    source_evidence = db.query(Evidence).filter(
        Evidence.id == analysis.source_evidence_id,
        Evidence.person_id == person_id,
        Evidence.type == "document",
    ).first()
    if source_evidence is None:
        raise HTTPException(status_code=409, detail="The original document evidence is unavailable in this care record")

    if candidate.get("source_evidence_id") not in (None, analysis.source_evidence_id):
        raise HTTPException(status_code=409, detail="Candidate provenance does not match the original document")

    source_quote = candidate.get("source_quote")
    if not isinstance(source_quote, str) or not source_quote.strip():
        raise HTTPException(status_code=409, detail="Candidate has no verifiable source quotation")
    source_start = candidate.get("source_span_start")
    source_end = candidate.get("source_span_end")
    if not isinstance(source_start, int) or not isinstance(source_end, int):
        raise HTTPException(status_code=409, detail="Candidate source offsets are missing")
    if source_end - source_start != len(source_quote) or (analysis.source_text or "")[source_start:source_end] != source_quote:
        raise HTTPException(status_code=409, detail="Candidate quotation and offsets do not match the persisted source text")

    version = (latest.decision_version if latest else 0) + 1
    assertion_id = None
    superseded_assertion_id = None
    question_id = None
    effective_subject = (
        (payload.corrected_subject or "").strip()
        or ((latest.corrected_subject or "").strip() if latest and latest.decision == "correct" else "")
        or (candidate.get("subject") or "Care recipient").strip()
    )
    effective_observation = (
        (payload.corrected_observation or "").strip()
        or ((latest.corrected_observation or "").strip() if latest and latest.decision == "correct" else "")
        or (candidate.get("observation") or "").strip()
    )
    if payload.decision in {"accept", "correct"} and not effective_observation:
        raise HTTPException(status_code=422, detail="The candidate has no observation text to review")

    # A correction is a proposed replacement, not approval. The prior accepted
    # assertion remains active until a separate explicit accept decision.
    if payload.decision == "clarify":
        question_id = str(uuid.uuid4())
        clarification = OpenQuestion(
            id=question_id,
            person_id=person_id,
            question=f"Please verify this statement from the uploaded document: {source_quote}",
            subject_type="care_recipient",
            subject_id=person_id,
            intended_answerer_role="caregiver",
            necessity="not_assessed",
            status="open",
            evidence_ids=[analysis.source_evidence_id],
            context=[{
                "kind": "document_candidate_clarification",
                "analysis_id": analysis_id,
                "candidate_index": payload.candidate_index,
                "source_quote": source_quote,
            }],
            created_by_caregiver_id=caregiver_id,
        )
        db.add(clarification)

    if payload.decision == "accept":
        previous_accepted = db.query(DocumentCandidateReview).filter(
            DocumentCandidateReview.analysis_id == analysis_id,
            DocumentCandidateReview.candidate_index == payload.candidate_index,
            DocumentCandidateReview.decision.in_(["accept"]),
            DocumentCandidateReview.resulting_assertion_id.is_not(None),
        ).order_by(DocumentCandidateReview.decision_version.desc()).with_for_update().first()
        if previous_accepted is not None:
            prior_assertion = db.query(CareAssertion).filter(
                CareAssertion.id == previous_accepted.resulting_assertion_id,
                CareAssertion.person_id == person_id,
            ).with_for_update().first()
            if prior_assertion is None:
                raise HTTPException(status_code=409, detail="The prior LCR assertion is unavailable; correction was not applied")
            prior_assertion.assertion_status = "superseded"
            superseded_assertion_id = prior_assertion.id

        assertion_id = str(uuid.uuid4())
        assertion = CareAssertion(
            id=assertion_id,
            person_id=person_id,
            subject_type="care_recipient",
            subject_id=person_id,
            field="document_observation",
            assertion_kind="caregiver_reviewed_document_report",
            asserted_value=f"{effective_subject}: {effective_observation}",
            source_event_id=None,
            source_label="Caregiver-reviewed document",
            source_text=source_quote,
            evidence_ids=[analysis.source_evidence_id],
            assertion_status="reported",
            created_by_caregiver_id=caregiver_id,
            updated_by_caregiver_id=caregiver_id,
        )
        db.add(assertion)

    row = DocumentCandidateReview(
        id=str(uuid.uuid4()),
        analysis_id=analysis_id,
        person_id=person_id,
        caregiver_id=caregiver_id,
        source_evidence_id=analysis.source_evidence_id,
        candidate_index=payload.candidate_index,
        decision_version=version,
        decision=payload.decision,
        original_candidate=dict(candidate),
        source_quote=source_quote,
        corrected_subject=(effective_subject if payload.decision in {"correct", "accept"} else None),
        corrected_observation=(effective_observation if payload.decision in {"correct", "accept"} else None),
        rationale=payload.rationale,
        resulting_assertion_id=assertion_id,
        superseded_assertion_id=superseded_assertion_id,
        resulting_question_id=question_id,
        idempotency_key=payload.idempotency_key,
    )
    db.add(row)
    try:
        db.commit()
        db.refresh(row)
    except IntegrityError as exc:
        db.rollback()
        if payload.idempotency_key:
            prior = db.query(DocumentCandidateReview).filter(
                DocumentCandidateReview.person_id == person_id,
                DocumentCandidateReview.idempotency_key == payload.idempotency_key,
            ).first()
            if prior is not None:
                return _decision_response(prior)
        raise HTTPException(status_code=409, detail="A concurrent review was recorded; reload the candidate history") from exc
    return _decision_response(row)



@router.post("/analyses/{analysis_id}/interpret")
def retry_document_interpretation(
    analysis_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Retry candidate interpretation only while no caregiver decisions exist."""
    person_id, caregiver_id = _identity(request, db)
    analysis = db.query(CaptureAnalysis).filter(
        CaptureAnalysis.id == analysis_id,
        CaptureAnalysis.person_id == person_id,
    ).with_for_update().first()
    if analysis is None:
        raise HTTPException(status_code=404, detail="Document extraction not found")
    metadata = dict(analysis.candidates) if isinstance(analysis.candidates, dict) else {}
    if metadata.get("kind") != "document_extraction_v1":
        raise HTTPException(status_code=404, detail="Document extraction not found")
    existing_reviews = db.query(DocumentCandidateReview.id).filter(
        DocumentCandidateReview.analysis_id == analysis_id,
        DocumentCandidateReview.person_id == person_id,
    ).first()
    if existing_reviews is not None:
        raise HTTPException(status_code=409, detail="Interpretation cannot be replaced after caregiver review has begun")

    attempts = list(metadata.get("candidate_extraction_attempts") or [])
    source_text = analysis.source_text or ""
    if not source_text:
        raise HTTPException(status_code=422, detail="No extracted source text is available to interpret")
    try:
        candidates = extract_review_candidates(
            source_text,
            source_event_id=None,
            source_evidence_id=analysis.source_evidence_id,
        )
    except Exception as exc:
        attempts.append({
            "attempt": len(attempts) + 1,
            "status": "unavailable",
            "attempted_by_caregiver_id": caregiver_id,
            "attempted_at": datetime.now(timezone.utc).isoformat(),
            "candidate_count": 0,
            "source_text_sha256": hashlib.sha256(source_text.encode("utf-8")).hexdigest(),
        })
        metadata["candidate_extraction_attempts"] = attempts
        metadata["candidate_extraction_status"] = "unavailable"
        analysis.candidates = metadata
        db.commit()
        raise HTTPException(status_code=503, detail="Candidate interpretation is unavailable; the original and extracted text remain preserved") from exc

    observations = candidates.get("observations", [])
    metadata["observations"] = observations
    metadata["open_questions"] = candidates.get("open_questions", [])
    metadata["candidate_extraction_status"] = "available"
    metadata["candidate_extraction_source_chars"] = min(len(source_text), 12_000)
    metadata["candidate_extraction_truncated"] = len(source_text) > 12_000
    attempts.append({
        "attempt": len(attempts) + 1,
        "status": "available",
        "attempted_by_caregiver_id": caregiver_id,
        "attempted_at": datetime.now(timezone.utc).isoformat(),
        "candidate_count": len(observations),
        "source_text_sha256": hashlib.sha256(source_text.encode("utf-8")).hexdigest(),
    })
    metadata["candidate_extraction_attempts"] = attempts
    analysis.candidates = metadata
    db.commit()
    db.refresh(analysis)
    return {
        "analysis_id": analysis_id,
        "candidate_extraction_status": "available",
        "candidate_count": len(observations),
        "open_question_count": len(metadata["open_questions"]),
        "attempt_count": len(attempts),
        "review_required": True,
        "written_to_lcr": False,
    }

@router.get("/analyses/{analysis_id}/reviews")
def get_document_candidate_reviews(
    analysis_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    person_id, caregiver_id = _identity(request, db)
    analysis = db.query(CaptureAnalysis).filter(
        CaptureAnalysis.id == analysis_id,
        CaptureAnalysis.person_id == person_id,
    ).first()
    if analysis is None:
        raise HTTPException(status_code=404, detail="Document extraction not found")
    rows = db.query(DocumentCandidateReview).filter(
        DocumentCandidateReview.analysis_id == analysis_id,
        DocumentCandidateReview.person_id == person_id,
    ).order_by(
        DocumentCandidateReview.candidate_index,
        DocumentCandidateReview.decision_version,
    ).all()
    return {
        "analysis_id": analysis_id,
        "reviews": [_decision_response(row) for row in rows],
        "review_count": len(rows),
    }


@router.get("/intelligence")
def list_document_intelligence(
    request: Request,
    db: Session = Depends(get_db),
):
    """List only the authenticated person's persisted document analyses and latest review state."""
    person_id, caregiver_id = _identity(request, db)
    analyses = db.query(CaptureAnalysis).filter(
        CaptureAnalysis.person_id == person_id,
    ).order_by(CaptureAnalysis.created_at.desc()).limit(100).all()
    items = []
    for analysis in analyses:
        metadata = analysis.candidates if isinstance(analysis.candidates, dict) else {}
        if metadata.get("kind") != "document_extraction_v1":
            continue
        reviews = db.query(DocumentCandidateReview).filter(
            DocumentCandidateReview.analysis_id == analysis.id,
            DocumentCandidateReview.person_id == person_id,
        ).order_by(
            DocumentCandidateReview.candidate_index,
            DocumentCandidateReview.decision_version.desc(),
        ).all()
        latest_by_index = {}
        for review in reviews:
            latest_by_index.setdefault(review.candidate_index, review)

        observations = []
        for index, raw_candidate in enumerate(metadata.get("observations", [])):
            if not isinstance(raw_candidate, dict):
                continue
            candidate = dict(raw_candidate)
            latest = latest_by_index.get(index)
            if latest is None:
                candidate["review_status"] = "pending_review"
            elif latest.decision == "accept":
                candidate["review_status"] = "accepted_by_caregiver"
                candidate["review_decision"] = "accept"
                candidate["reviewed_by_caregiver_id"] = latest.caregiver_id
                candidate["review_id"] = latest.id
                candidate["review_version"] = latest.decision_version
                candidate["resulting_assertion_id"] = latest.resulting_assertion_id
                candidate["subject"] = latest.corrected_subject or candidate.get("subject")
                candidate["observation"] = latest.corrected_observation or candidate.get("observation")
            elif latest.decision == "reject":
                candidate["review_status"] = "rejected_by_caregiver"
                candidate["review_decision"] = "reject"
                candidate["review_id"] = latest.id
                candidate["review_version"] = latest.decision_version
            elif latest.decision == "correct":
                candidate["review_status"] = "corrected_pending_approval"
                candidate["review_decision"] = "correct"
                candidate["review_id"] = latest.id
                candidate["review_version"] = latest.decision_version
                candidate["subject"] = latest.corrected_subject or candidate.get("subject")
                candidate["observation"] = latest.corrected_observation or candidate.get("observation")
            else:
                candidate["review_status"] = "clarification_requested"
                candidate["review_decision"] = "clarify"
                candidate["review_id"] = latest.id
                candidate["review_version"] = latest.decision_version
                candidate["resulting_question_id"] = latest.resulting_question_id
            observations.append(candidate)

        items.append({
            "analysis_id": analysis.id,
            "source_evidence_id": analysis.source_evidence_id,
            "document_name": metadata.get("filename") or "Uploaded document",
            "extraction_status": analysis.extraction_status,
            "extraction_method": metadata.get("extraction_method"),
            "pages": metadata.get("pages", []),
            "observations": observations,
            "open_questions": metadata.get("open_questions", []),
            "candidate_extraction_status": metadata.get("candidate_extraction_status", "not_applicable"),
            "review_required": any(
                item.get("review_status") in {"pending_review", "corrected_pending_approval", "clarification_requested"}
                for item in observations
            ),
            "written_to_lcr": False,
        })
    return {"items": items, "count": len(items)}
