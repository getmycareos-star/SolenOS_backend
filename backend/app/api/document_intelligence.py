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
import re
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import inspect
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.api.document_lifecycle import (
    _verify_persisted_extraction,
    _ocr_quality_by_page,
    _page_count,
    _unprocessed_page_numbers,
    _retryable_page_numbers,
    _blocked_page_numbers,
    _unreadable_page_numbers,
)
from app.services.care_continuity_execution import execute_care_continuity_reconciliation
from app.models.anonymous_session import AnonymousSession
from app.models.care import Evidence
from app.models.care_continuity import CareAssertion
from app.models.capture_analysis import CaptureAnalysis, DocumentReviewEvent
from app.models.qwen_background_job import QwenBackgroundJob, QwenJobEvent
from app.models.open_loop import OpenLoopRecord, OpenLoopEvent, OpenLoopOccurrence
from app.models.uncertainty import OpenQuestion
from app.core.uncertainty_enums import Priority
from app.schemas.care_continuity import CareAssertionCreate
from app.services.care_continuity import create_assertion
from app.models.collaboration import EvidenceUpload
from app.services.qwen_gateway import extract_review_candidates, get_ollama_config
from app.services.uncertainty import persist_source_clarification_questions
from app.services.document_candidate_schema import canonical_candidate, candidate_warnings, medication_details_for_candidate, classify_field, split_document_pages, chunk_document_pages, apply_medication_conflict_warnings, unreadable_page_numbers

router = APIRouter(prefix="/documents", tags=["document intelligence"])
logger = logging.getLogger(__name__)
MAX_DOCUMENT_QWEN_PAGES = 25
MAX_DOCUMENT_QWEN_CHUNKS = 60
MAX_DOCUMENT_CANDIDATE_BATCH = 60


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


def _require_complete_extraction(analysis: CaptureAnalysis) -> None:
    """Do not interpret or accept facts from a document with missing page content."""
    source_text = analysis.source_text or ""
    unreadable_pages = _unreadable_page_numbers(source_text)
    unprocessed_pages = _unprocessed_page_numbers(source_text)
    if analysis.extraction_status != "available" or unreadable_pages or unprocessed_pages:
        details = []
        if unreadable_pages:
            details.append("unreadable pages: " + ", ".join(map(str, unreadable_pages)))
        if unprocessed_pages:
            details.append("pages not yet processed: " + ", ".join(map(str, unprocessed_pages)))
        reason = "; ".join(details) or "the extraction is not marked complete"
        raise HTTPException(
            status_code=409,
            detail=(
                "Document interpretation is blocked until every page has a complete extraction. "
                f"Found {reason}. Retry failed pages or upload a clearer copy before review."
            ),
        )


def _source(evidence_id: str, person_id: str, caregiver_id: str, db: Session):
    evidence = db.query(Evidence).filter(
        Evidence.id == evidence_id,
        Evidence.person_id == person_id,
        Evidence.uploaded_by_caregiver_id == caregiver_id,
        Evidence.type == "document",
    ).with_for_update().first()
    if evidence is None or evidence.evidence_status == "deleted_by_caregiver":
        raise HTTPException(status_code=404, detail="Saved document not found in this caregiver's care record")
    upload = db.query(EvidenceUpload).filter(EvidenceUpload.evidence_id == evidence_id).first()
    if upload is None:
        raise HTTPException(status_code=409, detail="The original uploaded document is unavailable")
    content = bytes(upload.content)
    digest = hashlib.sha256(content).hexdigest()
    if len(content) != upload.size_bytes or digest != upload.sha256:
        raise HTTPException(status_code=500, detail="Stored evidence failed its integrity check")
    return evidence, upload


def _duplicate_of(evidence: Evidence) -> str | None:
    raw = evidence.extra_metadata
    try:
        details = raw if isinstance(raw, dict) else json.loads(raw or "{}")
    except (TypeError, ValueError):
        return None
    value = details.get("duplicate_of_evidence_id") if isinstance(details, dict) else None
    return str(value) if value else None


def _document_assertion_field(candidate: dict) -> str:
    field_type = str(candidate.get("field_type") or "observation")
    if field_type not in {"medication", "allergy", "diagnosis", "appointment", "care_event", "observation"}:
        field_type = "observation"
    # Canonical care fields allow document evidence to reconcile against existing LCR assertions.
    return field_type


def _document_asserted_value(candidate: dict, subject: str, observation: str) -> str:
    canonical = candidate.get("canonical_candidate")
    canonical = canonical if isinstance(canonical, dict) else {}
    canonical_warnings = canonical.get("warnings")
    candidate_warnings_list = candidate.get("warnings")
    warning_values = []
    if isinstance(canonical_warnings, list):
        warning_values.extend(str(value) for value in canonical_warnings if value)
    if isinstance(candidate_warnings_list, list):
        warning_values.extend(str(value) for value in candidate_warnings_list if value)
    warnings = list(dict.fromkeys(warning_values))
    warning_text = (
        "\nReview warnings: " + "; ".join(warnings[:5])
        if warnings else ""
    )
    if candidate.get("field_type") == "medication":
        details = canonical.get("medication_details")
        details = details if isinstance(details, dict) else {}
        return (
            f"Medication: {subject}\n"
            f"Strength: {details.get('strength') or 'Not stated in source'}\n"
            f"Dosage instructions: {details.get('dosage_instructions') or 'Not stated in source'}\n"
            f"Event date: {candidate.get('event_date') or 'Not stated or unresolved'}\n"
            f"Source-recorded date: {candidate.get('recorded_date') or 'Not stated in document'}\n"
            f"Source statement: {observation}{warning_text}"
        )
    return (
        f"Subject: {subject}\n"
        f"Statement: {observation}\n"
        f"Event date: {candidate.get('event_date') or 'Not stated or unresolved'}\n"
        f"Source-recorded date: {candidate.get('recorded_date') or 'Not stated in document'}{warning_text}"
    )


def _existing_assertion_for_exact_document_duplicate(
    db: Session,
    *,
    person_id: str,
    caregiver_id: str,
    evidence_id: str,
    upload: EvidenceUpload,
    field: str,
    source_quote: str,
    asserted_value: str,
    subject_type: str = "person",
    subject_id: str | None = None,
    assertion_kind: str = "document_reviewed_observation",
):
    """Reuse a prior assertion only when the uploaded binaries are exact duplicates."""
    if subject_id is None and subject_type == "person":
        subject_id = person_id
    existing_query = db.query(CareAssertion).filter(
        CareAssertion.person_id == person_id,
        CareAssertion.field == field,
        CareAssertion.assertion_kind == assertion_kind,
        CareAssertion.subject_type == subject_type,
        CareAssertion.source_text == source_quote,
    )
    if subject_id is not None:
        existing_query = existing_query.filter(CareAssertion.subject_id == subject_id)
    existing = existing_query.order_by(CareAssertion.recorded_at.desc()).all()
    duplicate_group_ids = {
        str(row[0])
        for row in db.query(EvidenceUpload.evidence_id)
        .join(Evidence, Evidence.id == EvidenceUpload.evidence_id)
        .filter(
            Evidence.person_id == person_id,
            Evidence.type == "document",
            EvidenceUpload.sha256 == upload.sha256,
        ).all()
    }
    duplicate_group_ids.add(evidence_id)
    active_duplicate_assertions = [
        row for row in existing
        if duplicate_group_ids.intersection(str(value) for value in (row.evidence_ids or []))
        and row.assertion_status not in {"rejected", "superseded"}
    ]
    assertion_row = next(
        (row for row in active_duplicate_assertions if row.asserted_value == asserted_value),
        None,
    )
    if active_duplicate_assertions and assertion_row is None:
        raise HTTPException(
            status_code=409,
            detail="An exact duplicate document already has a different reviewed interpretation of this passage. No second Living Care Record assertion was created.",
        )
    return assertion_row


def _persist_document_follow_up(
    db: Session,
    *,
    person_id: str,
    caregiver_id: str,
    evidence_id: str,
    analysis_id: str,
    upload: EvidenceUpload,
    candidate: dict,
    subject: str,
    observation: str,
    quote: str,
) -> str | None:
    """Create an existing Open Loop only for a clear, caregiver-accepted follow-up recommendation."""
    if candidate.get("field_type") != "appointment":
        return None
    searchable = " ".join((subject, observation, quote)).casefold()
    actionable_phrase = any(term in searchable for term in (
        "follow-up", "follow up", "clinic review", "follow-up review",
        "review within", "schedule a review", "arrange a review",
    ))
    recommendation_phrase = any(term in searchable for term in (
        "recommend", "recommended", "should arrange", "follow-up", "follow up",
        "review within", "schedule a review", "arrange a review",
    ))
    if not actionable_phrase or not recommendation_phrase:
        return None

    source_fingerprint = hashlib.sha256(
        f"{upload.sha256}|{quote}|document_follow_up".encode("utf-8")
    ).hexdigest()
    source_id = f"document-follow-up:{source_fingerprint}"
    existing = db.query(OpenLoopRecord).join(
        OpenLoopOccurrence, OpenLoopOccurrence.loop_id == OpenLoopRecord.id
    ).filter(
        OpenLoopRecord.person_id == person_id,
        OpenLoopRecord.loop_type == "follow_up",
        OpenLoopOccurrence.source_type == "document_candidate",
        OpenLoopOccurrence.source_id == source_id,
    ).first()
    if existing is not None:
        return str(existing.id)

    page_number = candidate.get("page_number")
    evidence = [{
        "kind": "document_candidate",
        "evidence_id": evidence_id,
        "analysis_id": analysis_id,
        "page_number": page_number,
        "source_quote": quote,
        "verification": "caregiver_accepted_document_candidate",
    }]
    now = datetime.now(timezone.utc)
    title = (
        "Arrange the recommended clinic review"
        if "clinic" in searchable or "review" in searchable
        else "Follow up on the hospital's recommendation"
    )
    description = (
        "The hospital document recommends a follow-up. "
        "The source wording is preserved below; this does not mean an appointment has been booked.\n\n"
        f"Source statement: {quote}"
    )
    loop = OpenLoopRecord(
        person_id=person_id,
        caregiver_id=caregiver_id,
        loop_type="follow_up",
        title=title,
        description=description,
        evidence=evidence,
        uncertainty="unknown",
        source_type="document_candidate",
        source_id=source_id,
        source_fingerprint=source_fingerprint,
        status="open",
        priority="normal",
        created_at=now,
        updated_at=now,
        version=1,
    )
    db.add(loop)
    db.flush()
    occurrence = OpenLoopOccurrence(
        loop_id=loop.id,
        person_id=person_id,
        caregiver_id=caregiver_id,
        source_type="document_candidate",
        source_id=source_id,
        source_fingerprint=source_fingerprint,
        evidence=evidence,
        idempotency_key=source_id,
        occurred_at=now,
        created_at=now,
    )
    db.add(occurrence)
    db.add(OpenLoopEvent(
        loop_id=loop.id,
        person_id=person_id,
        caregiver_id=caregiver_id,
        event_type="created_from_document_review",
        before_state=None,
        after_state={
            "id": loop.id,
            "loop_type": "follow_up",
            "title": title,
            "description": description,
            "status": "open",
            "source_type": "document_candidate",
            "source_id": source_id,
            "evidence": evidence,
            "uncertainty": "unknown",
            "created_at": now.isoformat(),
        },
        evidence=evidence,
        idempotency_key=source_id,
        actor=caregiver_id,
        created_at=now,
    ))
    db.flush()
    return str(loop.id)


def _persist_medication_document_conflict_question(
    db: Session,
    *,
    person_id: str,
    caregiver_id: str,
    evidence_id: str,
    analysis_id: str,
    upload: EvidenceUpload,
    candidate: dict,
    active_medication: dict,
) -> str | None:
    """Record a professional clarification when source text conflicts with a current LCR medication."""
    if candidate.get("field_type") != "medication":
        return None
    subject = str(candidate.get("subject") or "").strip()
    quote = str(candidate.get("source_quote") or "").strip()
    observation = str(candidate.get("observation") or "").strip()
    if not subject or not quote:
        return None
    change_language = re.search(
        r"\b(discontinued|discontinue|stopped|stop|held|hold|suspend|dose changed|new dose|dose increased|dose decreased|dose reduced)\b",
        (quote + " " + observation).casefold(),
    )
    if not change_language:
        return None
    normalize = lambda value: re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())
    if normalize(subject) != normalize(active_medication.get("name")):
        return None
    medication_id = str(active_medication.get("medication_id") or "")
    if not medication_id:
        return None
    active_status_claim = db.query(CareAssertion).filter(
        CareAssertion.person_id == person_id,
        CareAssertion.subject_id == medication_id,
        CareAssertion.subject_type.in_(("medication", "medications")),
        CareAssertion.field.in_(("status", "medication_status")),
        CareAssertion.assertion_kind == "current_state",
        CareAssertion.assertion_status.in_(("accepted", "caregiver_reviewed")),
    ).order_by(CareAssertion.recorded_at.desc(), CareAssertion.id.desc()).all()
    active_status_claim = next(
        (
            row for row in active_status_claim
            if str(row.asserted_value or "").strip().casefold() == "active"
            and row.epistemic_state in {"reported", "observed", "verified"}
        ),
        None,
    )
    if active_status_claim is None:
        return None

    existing_questions = db.query(OpenQuestion).filter(
        OpenQuestion.person_id == person_id,
        OpenQuestion.subject_type == "medication",
        OpenQuestion.subject_id == medication_id,
        OpenQuestion.intended_answerer_role == "professional",
        OpenQuestion.status == "open",
    ).all()
    for question in existing_questions:
        context_items = question.context if isinstance(question.context, list) else []
        same_source = any(
            isinstance(item, dict) and item.get("source_sha256") == upload.sha256
            for item in context_items
        )
        if evidence_id in (question.evidence_ids or []) or same_source:
            # Identical uploads remain separate evidence records. Preserve the
            # duplicate's lineage on the existing professional question so
            # every document status row reports the conflict and deletion of
            # either source cannot silently orphan the clarification.
            linked_evidence_ids = list(dict.fromkeys([
                *(str(value) for value in (question.evidence_ids or [])),
                evidence_id,
            ]))
            if linked_evidence_ids != list(question.evidence_ids or []):
                question.evidence_ids = linked_evidence_ids
            if not any(
                isinstance(item, dict) and item.get("document_evidence_id") == evidence_id
                for item in context_items
            ):
                context_items.append({
                    "source_kind": "duplicate_document_candidate",
                    "analysis_id": analysis_id,
                    "document_evidence_id": evidence_id,
                    "source_sha256": upload.sha256,
                    "page_number": candidate.get("page_number"),
                    "candidate_subject": subject,
                    "candidate_observation": observation,
                    "source_quote": quote,
                    "interpretation": "same_binary_document_duplicate_linked_to_existing_professional_question",
                })
                question.context = context_items
            db.add(question)
            db.flush()
            return str(question.id)

    evidence_ids = list(dict.fromkeys([
        evidence_id,
        *(str(value) for value in (active_medication.get("evidence_ids") or [])),
        *(str(value) for value in (active_status_claim.evidence_ids or [])),
    ]))
    necessity_rationale = (
        "A source-grounded hospital document candidate conflicts with a current, caregiver-reviewed medication record. "
        "The current instruction requires professional confirmation before any medication decision."
    )
    context = [{
        "source_kind": "document_extraction",
        "source_id": evidence_id,
        "source_record_refs": [{"record_type": "evidence", "record_id": evidence_id}],
        "necessity_assessment": {
            "necessity_rationale": necessity_rationale,
            "decision_dependency": (
                "The current medication instruction cannot be represented accurately in the care record "
                "until the prescribing clinician or pharmacist confirms whether the hospital document reflects a change."
            ),
            "evidence_ids": list(evidence_ids),
        },
        "analysis_id": analysis_id,
        "document_evidence_id": evidence_id,
        "source_sha256": upload.sha256,
        "page_number": candidate.get("page_number"),
        "candidate_subject": subject,
        "candidate_observation": observation,
        "source_quote": quote,
        "existing_medication_assertion_id": active_status_claim.id,
        "existing_medication_name": active_medication.get("name"),
        "existing_medication_dose": active_medication.get("dose"),
        "existing_medication_review_after_days": active_medication.get("review_after_days"),
        "interpretation": "unconfirmed_document_candidate_against_current_lcr_medication",
    }]
    question = OpenQuestion(
        person_id=person_id,
        question=(
            f"The hospital document appears to list {subject} as changed or discontinued, "
            f"while the current care record lists it as active. What is the current prescribing instruction? "
            "Please confirm with the prescribing clinician or pharmacist before administering. "
            "SolenOS has not advised stopping, restarting, or changing this medicine."
        ),
        subject_type="medication",
        subject_id=medication_id,
        claim_id=active_status_claim.id,
        intended_answerer_role="professional",
        intended_answerer_label="Prescribing professional or pharmacist",
        necessity="necessary",
        necessity_rationale=necessity_rationale,
        status="open",
        priority=Priority.HIGH,
        evidence_ids=evidence_ids,
        context=context,
        created_by_caregiver_id=caregiver_id,
    )
    db.add(question)
    db.flush()
    return str(question.id)


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
    source_text = analysis.source_text or ""
    page_count = _page_count(source_text)
    unprocessed_pages = _unprocessed_page_numbers(source_text)
    retryable_pages = _retryable_page_numbers(source_text)
    blocked_pages = _blocked_page_numbers(source_text)
    unreadable_pages = _unreadable_page_numbers(source_text)
    processed_pages = [number for number in range(1, page_count + 1) if number not in unprocessed_pages]
    stored_observations = intelligence.get("observations") if isinstance(intelligence.get("observations"), list) else []
    all_observations = stored_observations
    extraction_complete = (
        analysis.extraction_status == "available"
        and not _unprocessed_page_numbers(source_text)
        and not _unreadable_page_numbers(source_text)
    )
    if not extraction_complete:
        # Never expose previously generated candidates for a document whose
        # current persisted extraction is incomplete. Review writes are also
        # blocked independently in review_document_candidate.
        all_observations = []
    visible_indexes = intelligence.get("visible_candidate_indexes")
    if not isinstance(visible_indexes, list):
        visible_indexes = list(range(min(MAX_DOCUMENT_CANDIDATE_BATCH, len(all_observations))))
    visible_indexes = [
        index for index in visible_indexes
        if isinstance(index, int) and 0 <= index < len(all_observations)
    ]
    observations = [
        {**all_observations[index], "candidate_index": index}
        for index in visible_indexes
        if isinstance(all_observations[index], dict)
    ]
    next_candidate_offset = intelligence.get("next_candidate_offset")
    if not isinstance(next_candidate_offset, int) or next_candidate_offset < 0:
        next_candidate_offset = min(MAX_DOCUMENT_CANDIDATE_BATCH, len(all_observations))
    has_more_candidates = next_candidate_offset < len(all_observations)
    return {
        "analysis_id": str(analysis.id),
        "source_evidence_id": str(analysis.source_evidence_id),
        "document_name": metadata.get("filename") or "Uploaded document",
        "document_type": metadata.get("document_type") or "general_care_document",
        "page_count": metadata.get("page_count") or page_count,
        "extraction_status": analysis.extraction_status,
        "intelligence_status": intelligence.get("status") or "not_run",
        "processed_page_count": intelligence.get("processed_page_count"),
        "total_page_count": intelligence.get("total_page_count") or metadata.get("page_count") or page_count,
        "processed_page_numbers": intelligence.get("processed_page_numbers", []),
        "unprocessed_page_numbers": intelligence.get("unprocessed_page_numbers", []),
        "extraction_processed_page_count": len(processed_pages),
        "extraction_processed_page_numbers": processed_pages,
        "extraction_unprocessed_page_numbers": unprocessed_pages,
        "retryable_page_numbers": retryable_pages,
        "blocked_page_numbers": blocked_pages,
        "unreadable_page_numbers": unreadable_pages,
        "ocr_quality_by_page": _ocr_quality_by_page(analysis.source_text or ""),
        "model_provenance": intelligence.get("model_provenance"),
        "processing_history": intelligence.get("processing_history", []),
        "candidate_overflow": has_more_candidates,
        "has_more_candidates": has_more_candidates,
        "remaining_candidate_count": max(0, len(all_observations) - next_candidate_offset),
        "extraction_method": metadata.get("extraction_method"),
        "extracted_char_count": len(analysis.source_text or ""),
        "observations": observations,
        "open_questions": intelligence.get("open_questions", []),
        "interpretation_blocked": not extraction_complete,
        "review_required": extraction_complete and any(
            isinstance(item, dict) and item.get("review_status") in {"pending_review", "corrected_pending_approval"}
            for item in all_observations
        ),
        "written_to_lcr": any(
            isinstance(item, dict) and item.get("review_status") == "accepted_by_caregiver"
            for item in stored_observations
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
    seen_evidence_ids = set()
    for analysis in rows:
        metadata = analysis.candidates if isinstance(analysis.candidates, dict) else {}
        if metadata.get("kind") != "document_extraction_v1":
            continue
        evidence_id = str(analysis.source_evidence_id or "")
        if not evidence_id or evidence_id in seen_evidence_ids:
            continue
        evidence = db.query(Evidence).filter(
            Evidence.id == analysis.source_evidence_id,
            Evidence.person_id == person_id,
            Evidence.uploaded_by_caregiver_id == caregiver_id,
            Evidence.type == "document",
            (Evidence.evidence_status.is_(None)) | (Evidence.evidence_status != "deleted_by_caregiver"),
        ).first()
        if evidence is None:
            continue
        seen_evidence_ids.add(evidence_id)
        items.append(_response(analysis))
        if len(items) >= limit:
            break
    processing_jobs = []
    seen_evidence_ids = set()
    jobs = []
    if inspect(db.get_bind()).has_table("qwen_background_jobs"):
        jobs = db.query(QwenBackgroundJob).filter(
            QwenBackgroundJob.person_id == person_id,
            QwenBackgroundJob.caregiver_id == caregiver_id,
            QwenBackgroundJob.workflow == "document_intake",
            QwenBackgroundJob.status.in_(["queued", "processing", "failed"]),
        ).order_by(QwenBackgroundJob.created_at.desc()).limit(500).all()
    for job in jobs:
        try:
            evidence_ids = json.loads(job.evidence_ids_json or "[]")
        except (TypeError, ValueError):
            continue
        if not isinstance(evidence_ids, list):
            continue
        for evidence_id in evidence_ids:
            if not isinstance(evidence_id, str) or evidence_id in seen_evidence_ids:
                continue
            seen_evidence_ids.add(evidence_id)
            # The job is scoped to this same person and caregiver. Confirm the
            # evidence still belongs to that scope before exposing job status.
            owned = db.query(Evidence.id).filter(
                Evidence.id == evidence_id,
                Evidence.person_id == person_id,
                Evidence.uploaded_by_caregiver_id == caregiver_id,
                Evidence.type == "document",
            ).first()
            if owned is None:
                continue
            processing_jobs.append({
                "evidence_id": evidence_id,
                "job_id": job.id,
                "status": job.status,
                "attempt_count": job.attempt_count,
                "error_code": job.error_code,
            })
    return {
        "items": items,
        "count": len(items),
        "processing_jobs": processing_jobs,
        "identity_scope": "server_session_person_and_caregiver",
    }


@router.post("/{evidence_id}/queue", status_code=202)
def queue_document_intake(evidence_id: str, request: Request, db: Session = Depends(get_db)):
    """Queue or resume document OCR and understanding without blocking the caregiver."""
    person_id, caregiver_id = _identity(request, db)
    _evidence, _upload = _source(evidence_id, person_id, caregiver_id, db)

    job = db.query(QwenBackgroundJob).filter(
        QwenBackgroundJob.person_id == person_id,
        QwenBackgroundJob.caregiver_id == caregiver_id,
        QwenBackgroundJob.workflow == "document_intake",
        QwenBackgroundJob.evidence_ids_json == json.dumps([evidence_id]),
    ).order_by(QwenBackgroundJob.created_at.desc()).first()

    if job is not None and job.status in {"queued", "processing"}:
        return {
            "job_id": job.id,
            "evidence_id": evidence_id,
            "status": job.status,
            "idempotent_replay": True,
        }

    previous_status = job.status if job is not None else None
    if job is None:
        job = QwenBackgroundJob(
            person_id=person_id,
            caregiver_id=caregiver_id,
            task_text="Process saved document for caregiver review.",
            context_text="",
            evidence_ids_json=json.dumps([evidence_id]),
            workflow="document_intake",
            status="queued",
        )
        db.add(job)
        db.flush()
    else:
        job.status = "queued"
        job.attempt_count = 0
        job.available_at = datetime.now(timezone.utc)
        job.locked_at = None
        job.completed_at = None
        job.error_code = None
        job.result_text = None

    db.add(QwenJobEvent(
        job_id=job.id,
        person_id=person_id,
        caregiver_id=caregiver_id,
        event_type="document_intake_retried" if previous_status else "document_intake_queued",
        from_status=previous_status,
        to_status="queued",
        metadata_json={"source_evidence_id": evidence_id, "written_to_lcr": False},
    ))
    db.commit()
    db.refresh(job)
    return {
        "job_id": job.id,
        "evidence_id": evidence_id,
        "status": job.status,
        "idempotent_replay": False,
    }


@router.post("/{evidence_id}/intelligence", status_code=201)
def analyze_saved_document(evidence_id: str, request: Request, db: Session = Depends(get_db)):
    person_id, caregiver_id = _identity(request, db)
    evidence, upload = _source(evidence_id, person_id, caregiver_id, db)
    analysis = _analysis_for_evidence(evidence_id, person_id, caregiver_id, db)
    metadata = _metadata(analysis)
    duplicate_of_evidence_id = _duplicate_of(evidence)
    if metadata.get("binary_sha256") != upload.sha256:
        raise HTTPException(status_code=409, detail="The extraction does not match the preserved original file")
    _verify_persisted_extraction(analysis, upload, db)
    _require_complete_extraction(analysis)
    metadata = _metadata(analysis)
    existing = _intelligence(analysis)
    resume_intelligence = {}
    if existing:
        pending_pages = existing.get("unprocessed_page_numbers")
        stored_observations = existing.get("observations") if isinstance(existing.get("observations"), list) else []
        next_candidate_offset = existing.get("next_candidate_offset")
        if not isinstance(next_candidate_offset, int) or next_candidate_offset < 0:
            next_candidate_offset = min(MAX_DOCUMENT_CANDIDATE_BATCH, len(stored_observations))
        has_more_candidates = next_candidate_offset < len(stored_observations)
        if existing.get("status") == "partial" and (
            (isinstance(pending_pages, list) and pending_pages) or has_more_candidates
        ):
            resume_intelligence = existing
        else:
            try:
                persist_source_clarification_questions(
                    db,
                    person_id=person_id,
                    caregiver_id=caregiver_id,
                    source_kind="document_extraction",
                    source_record_type="evidence",
                    source_record_id=str(evidence.id),
                    evidence_ids=[str(evidence.id)],
                    questions=existing.get("open_questions") or [],
                )
                db.commit()
            except Exception:
                db.rollback()
                logger.exception("Could not backfill saved document clarification questions for evidence %s", evidence.id)
            return _response(analysis)

    # If page extraction is complete, reveal the next bounded candidate group
    # only after the caregiver has made a final decision on the current group.
    if resume_intelligence and not (resume_intelligence.get("unprocessed_page_numbers") or []):
        stored = resume_intelligence.get("observations") if isinstance(resume_intelligence.get("observations"), list) else []
        visible = resume_intelligence.get("visible_candidate_indexes")
        if not isinstance(visible, list):
            visible = list(range(min(MAX_DOCUMENT_CANDIDATE_BATCH, len(stored))))
        visible = [index for index in visible if isinstance(index, int) and 0 <= index < len(stored)]
        batch_final = all(
            stored[index].get("review_status") in {"accepted_by_caregiver", "rejected_by_caregiver"}
            for index in visible
            if isinstance(stored[index], dict)
        )
        next_offset = resume_intelligence.get("next_candidate_offset")
        if not isinstance(next_offset, int) or next_offset < 0:
            next_offset = min(MAX_DOCUMENT_CANDIDATE_BATCH, len(stored))
        if next_offset < len(stored) and batch_final:
            updated = copy.deepcopy(metadata)
            intelligence = updated["document_intelligence"]
            end = min(next_offset + MAX_DOCUMENT_CANDIDATE_BATCH, len(stored))
            intelligence["visible_candidate_indexes"] = list(range(next_offset, end))
            intelligence["next_candidate_offset"] = end
            intelligence["candidate_overflow"] = end < len(stored)
            intelligence["status"] = "partial" if intelligence["candidate_overflow"] or intelligence.get("unreadable_page_numbers") else "available"
            analysis.candidates = updated
            db.add(analysis)
            db.commit()
            db.refresh(analysis)
            return _response(analysis)
        if next_offset < len(stored):
            return _response(analysis)

    source_text = (analysis.source_text or "").strip()
    if not source_text:
        raise HTTPException(status_code=422, detail="No persisted extracted text is available; no document interpretation was generated")
    # Process each page independently so a long discharge packet cannot be
    # silently truncated to the first 12k characters by the Qwen gateway.
    raw_observations = []
    all_open_questions = []
    page_inputs = split_document_pages(source_text)
    unreadable_pages = unreadable_page_numbers(source_text)
    all_page_numbers = sorted({int(match.group(1)) for match in re.finditer(r"(?m)^\[Page\s+(\d+)", source_text)})
    if not page_inputs:
        raise HTTPException(status_code=422, detail="No readable page text is available for document understanding")

    if resume_intelligence:
        page_window_start = resume_intelligence.get("next_page_window_start")
        chunk_offset = resume_intelligence.get("next_chunk_offset", 0)
        if not isinstance(page_window_start, int) or page_window_start < 0:
            pending = set(resume_intelligence.get("unprocessed_page_numbers") or [])
            page_window_start = next(
                (index for index, (number, _) in enumerate(page_inputs) if number in pending),
                0,
            )
        if not isinstance(chunk_offset, int) or chunk_offset < 0:
            chunk_offset = 0
    else:
        page_window_start = 0
        chunk_offset = 0

    selected_pages = page_inputs[page_window_start:page_window_start + MAX_DOCUMENT_QWEN_PAGES]
    qwen_chunks = chunk_document_pages(selected_pages)
    selected_chunks = qwen_chunks[chunk_offset:chunk_offset + MAX_DOCUMENT_QWEN_CHUNKS]
    next_chunk_offset = chunk_offset + len(selected_chunks)
    if next_chunk_offset < len(qwen_chunks):
        next_page_window_start = page_window_start
        next_chunk_offset_to_store = next_chunk_offset
        unprocessed_pages = {number for number, _ in qwen_chunks[next_chunk_offset:]}
        unprocessed_pages.update(number for number, _ in page_inputs[page_window_start + len(selected_pages):])
    else:
        next_page_window_start = page_window_start + len(selected_pages)
        next_chunk_offset_to_store = 0
        unprocessed_pages = {number for number, _ in page_inputs[next_page_window_start:]}
    unprocessed_pages = sorted(unprocessed_pages)
    for page_number, page_text in selected_chunks:
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
    prior_observations = copy.deepcopy(resume_intelligence.get("observations") or [])
    grounded = prior_observations
    seen_quotes = {
        item.get("source_quote")
        for item in prior_observations
        if isinstance(item, dict) and isinstance(item.get("source_quote"), str)
    }
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
        if duplicate_of_evidence_id:
            canonical["warnings"].append(
                "This upload is an exact binary duplicate of an earlier evidence file; preserve the copy but do not count it as independent corroboration."
            )
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
            "extracted_at": canonical["extracted_at"],
            "confidence": canonical["confidence"],
            "warnings": canonical["warnings"],
            "canonical_candidate": canonical,
        })
    apply_medication_conflict_warnings(grounded)
    for candidate in grounded:
        canonical = candidate["canonical_candidate"]
        canonical["warnings"] = list(candidate.get("warnings") or canonical.get("warnings") or [])

    # Compare medication candidates only against the existing current-medication
    # projection, which already enforces same-person review windows and explicit
    # active status. A match creates a professional clarification, never a
    # medication change or an inferred administration instruction.
    try:
        from app.api.care_continuity import current_medication_records
        medication_projection = current_medication_records(request, db)
        active_medications = medication_projection.get("medications", [])
    except Exception:
        logger.exception("Current medication projection unavailable during document reconciliation")
        active_medications = []
        for candidate in grounded:
            if candidate.get("field_type") == "medication":
                candidate.setdefault("warnings", []).append(
                    "SolenOS could not compare this medication candidate with the current medication record. Do not treat it as a current instruction without professional confirmation."
                )
    for candidate in grounded:
        if candidate.get("field_type") != "medication":
            continue
        candidate_subject = re.sub(r"[^a-z0-9]+", "", str(candidate.get("subject") or "").casefold())
        matching_medication = next(
            (
                item for item in active_medications
                if candidate_subject
                and candidate_subject == re.sub(r"[^a-z0-9]+", "", str(item.get("name") or "").casefold())
            ),
            None,
        )
        # An exact duplicate upload may arrive after the first copy already
        # created a conflict question and the medication projection stopped
        # presenting either side as a current choice. Reuse that question's
        # subject mapping rather than losing the medication relationship.
        existing_source_question = None
        for question in db.query(OpenQuestion).filter(
            OpenQuestion.person_id == person_id,
            OpenQuestion.subject_type == "medication",
            OpenQuestion.intended_answerer_role == "professional",
            OpenQuestion.status == "open",
        ).all():
            context_items = question.context if isinstance(question.context, list) else []
            if any(
                isinstance(item, dict) and item.get("source_sha256") == upload.sha256
                for item in context_items
            ):
                existing_source_question = question
                question_context = next(
                    item for item in context_items
                    if isinstance(item, dict) and item.get("source_sha256") == upload.sha256
                )
                candidate["matched_lcr_medication_id"] = str(question.subject_id)
                candidate["matched_lcr_medication_name"] = question_context.get("existing_medication_name")
                candidate["matched_lcr_medication_review_after_days"] = question_context.get("existing_medication_review_after_days")
                candidate["professional_confirmation_question_id"] = str(question.id)
                break
        if matching_medication is None and existing_source_question is None:
            continue
        if matching_medication is not None:
            candidate["matched_lcr_medication_id"] = str(matching_medication.get("medication_id"))
            candidate["matched_lcr_medication_name"] = matching_medication.get("name")
            candidate["matched_lcr_medication_review_after_days"] = matching_medication.get("review_after_days")
            question_id = _persist_medication_document_conflict_question(
                db,
                person_id=person_id,
                caregiver_id=caregiver_id,
                evidence_id=evidence_id,
                analysis_id=analysis.id,
                upload=upload,
                candidate=candidate,
                active_medication=matching_medication,
            )
            if question_id:
                candidate["professional_confirmation_question_id"] = question_id
        if candidate.get("professional_confirmation_question_id"):
            candidate.setdefault("warnings", []).append(
                "This document appears to conflict with the current medication record. The instruction is unconfirmed; a prescribing professional or pharmacist must confirm it before any medication decision."
            )
            candidate["canonical_candidate"]["warnings"] = list(candidate["warnings"])

    metadata = copy.deepcopy(metadata)
    try:
        configured_model = get_ollama_config()[1]
        configured_provider = "private_ollama"
    except HTTPException:
        configured_model = None
        configured_provider = "unavailable_or_test_stub"
    model_provenance = {
        "provider": configured_provider,
        "model_tag": configured_model,
        "model_digest": None,
        "model_digest_status": "not_exposed_by_ollama_chat_response",
        "processing_version": "document_intelligence_v1",
        "prompt_version": "source_grounded_review_candidates_v1",
        "extraction_source": "persisted_server_extraction",
        "source_evidence_id": str(evidence.id),
        "source_text_sha256": hashlib.sha256(source_text.encode("utf-8")).hexdigest(),
        "processed_at": datetime.now(timezone.utc).isoformat(),
        "model_authority": "review_only",
    }
    prior_processing_history = resume_intelligence.get("processing_history")
    processing_history = list(prior_processing_history) if isinstance(prior_processing_history, list) else []
    prior_provenance = resume_intelligence.get("model_provenance")
    if isinstance(prior_provenance, dict) and (not processing_history or processing_history[-1] != prior_provenance):
        processing_history.append(prior_provenance)
    processing_history.append(model_provenance)
    previous_visible_indexes = resume_intelligence.get("visible_candidate_indexes")
    prior_observations = resume_intelligence.get("observations")
    prior_observations = prior_observations if isinstance(prior_observations, list) else []
    prior_next_candidate_offset = resume_intelligence.get("next_candidate_offset")
    if not isinstance(prior_next_candidate_offset, int) or prior_next_candidate_offset < 0:
        prior_next_candidate_offset = min(MAX_DOCUMENT_CANDIDATE_BATCH, len(prior_observations))
    if isinstance(previous_visible_indexes, list):
        visible_candidate_indexes = [
            index for index in previous_visible_indexes
            if isinstance(index, int) and 0 <= index < len(grounded)
        ]
        # Page-window resume can append candidates after the initial review
        # batch was formed. If every previously extracted candidate was
        # already visible and that batch has capacity, include the new
        # candidates now instead of falsely marking them as hidden overflow.
        if (
            len(visible_candidate_indexes) < MAX_DOCUMENT_CANDIDATE_BATCH
            and prior_next_candidate_offset >= len(prior_observations)
        ):
            visible_start = min(visible_candidate_indexes, default=0)
            visible_end = min(
                visible_start + MAX_DOCUMENT_CANDIDATE_BATCH,
                len(grounded),
            )
            visible_candidate_indexes = list(range(visible_start, visible_end))
            next_candidate_offset = visible_end
        else:
            next_candidate_offset = prior_next_candidate_offset
    else:
        visible_candidate_indexes = list(range(min(MAX_DOCUMENT_CANDIDATE_BATCH, len(grounded))))
        next_candidate_offset = min(MAX_DOCUMENT_CANDIDATE_BATCH, len(grounded))
    candidate_overflow = next_candidate_offset < len(grounded)
    metadata["document_intelligence"] = {
        "version": 1,
        "model_provenance": model_provenance,
        "processing_history": processing_history[-20:],
        "observations": grounded,
        "open_questions": list(dict.fromkeys([
            *[
                value for value in (resume_intelligence.get("open_questions") or [])
                if isinstance(value, str)
                and value.strip()
                and not value.startswith("Only the first ")
                and not value.startswith("Qwen reached the ")
                and not value.startswith("Pages ")
                and not value.startswith("Document understanding has more readable pages")
            ],
            *[
                value.strip()[:300]
                for value in all_open_questions[:3]
                if isinstance(value, str) and value.strip()
            ],
            *(
                [f"Document understanding has more readable pages to process. Remaining pages {', '.join(map(str, unprocessed_pages))} will be processed on retry."]
                if unprocessed_pages else []
            ),
            *(
                [f"Pages {', '.join(map(str, unreadable_pages))} have no readable extracted text. Review the original pages manually; no facts were inferred from them."]
                if unreadable_pages else []
            ),
            *(
                ["More source-backed statements are available after this group is reviewed."]
                if candidate_overflow else []
            ),
        ])),
        "processed_page_count": len(set(resume_intelligence.get("processed_page_numbers") or []) | {number for number, _ in selected_chunks}),
        "total_readable_page_count": len(page_inputs),
        "total_page_count": len(all_page_numbers) or len(page_inputs),
        "processed_page_numbers": sorted(set(resume_intelligence.get("processed_page_numbers") or []) | {number for number, _ in selected_chunks}),
        "unprocessed_page_numbers": unprocessed_pages,
        "unreadable_page_numbers": unreadable_pages,
        "next_page_window_start": next_page_window_start,
        "next_chunk_offset": next_chunk_offset_to_store,
        "candidate_overflow": candidate_overflow,
        "visible_candidate_indexes": visible_candidate_indexes,
        "next_candidate_offset": next_candidate_offset,
        "status": "partial" if (unprocessed_pages or unreadable_pages or candidate_overflow) else "available",
        "model_authority": "review_only",
        "source_text_sha256": hashlib.sha256(source_text.encode("utf-8")).hexdigest(),
    }
    analysis.candidates = metadata
    db.add(analysis)
    persist_source_clarification_questions(
        db,
        person_id=person_id,
        caregiver_id=caregiver_id,
        source_kind="document_extraction",
        source_record_type="evidence",
        source_record_id=str(evidence.id),
        evidence_ids=[str(evidence.id)],
        questions=metadata["document_intelligence"]["open_questions"],
    )
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
    _require_complete_extraction(analysis)
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
            if prior == "accept":
                # The append-only review event is the durable idempotency
                # source of truth. Candidate JSON is a compatibility projection
                # and may lag after a partial write/retry.
                prior_event = db.query(DocumentReviewEvent).filter(
                    DocumentReviewEvent.analysis_id == analysis_id,
                    DocumentReviewEvent.candidate_index == payload.candidate_index,
                    DocumentReviewEvent.decision == "accept",
                ).order_by(DocumentReviewEvent.decision_version.desc()).first()
                accepted_assertion_id = (
                    prior_event.resulting_assertion_id if prior_event
                    else candidate.get("accepted_assertion_id")
                )
                if accepted_assertion_id:
                    accepted_assertion = db.query(CareAssertion).filter(
                        CareAssertion.id == accepted_assertion_id,
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
    follow_up_id = None
    if payload.decision == "reject":
        candidate["review_status"] = "rejected_by_caregiver"
    elif payload.decision == "correct":
        subject = (payload.corrected_subject if payload.corrected_subject is not None else candidate.get("subject", "")).strip()
        observation = (payload.corrected_observation if payload.corrected_observation is not None else candidate.get("observation", "")).strip()
        if not subject or not observation:
            raise HTTPException(status_code=422, detail="A subject and corrected observation are required")
        candidate["subject"] = subject[:120]
        candidate["observation"] = observation[:500]
        candidate["field_type"] = classify_field(subject, observation, str(candidate.get("source_quote") or ""))
        canonical = candidate.get("canonical_candidate")
        if isinstance(canonical, dict):
            canonical["field_type"] = candidate["field_type"]
            canonical["extracted_value"] = observation[:500]
            canonical["review_status"] = "Corrected"
            if candidate["field_type"] == "medication":
                details = medication_details_for_candidate(
                    str(candidate.get("subject") or ""),
                    observation,
                )
                canonical["medication_details"] = {
                    "strength": details["strength"],
                    "dosage_instructions": details["dosage_instructions"],
                }
            else:
                canonical["medication_details"] = None
            canonical["warnings"] = candidate_warnings(
                candidate["field_type"],
                observation,
                str(candidate.get("source_quote") or ""),
                str(candidate.get("subject") or ""),
            )
            canonical["warnings"].append(
                "Caregiver-corrected wording requires review against the original source passage."
            )
            candidate["warnings"] = canonical["warnings"]
        candidate["review_status"] = "corrected_pending_approval"
    else:
        quote = candidate.get("source_quote")
        if not isinstance(quote, str) or not quote or quote not in (analysis.source_text or ""):
            raise HTTPException(status_code=409, detail="The candidate quotation is not present in the preserved extraction")
        canonical = candidate.get("canonical_candidate")
        canonical = canonical if isinstance(canonical, dict) else {}
        span_start = canonical.get("source_span_start")
        span_end = canonical.get("source_span_end")
        source_text = analysis.source_text or ""
        if (
            not isinstance(span_start, int)
            or not isinstance(span_end, int)
            or span_start < 0
            or span_end <= span_start
            or span_end > len(source_text)
            or source_text[span_start:span_end] != quote
            or canonical.get("source_excerpt") != quote
        ):
            raise HTTPException(
                status_code=409,
                detail="The candidate does not have a unique, verifiable source span; no LCR assertion was created",
            )
        subject = str(candidate.get("subject") or "").strip()
        observation = str(candidate.get("observation") or "").strip()
        if not subject or not observation:
            raise HTTPException(status_code=422, detail="The candidate is incomplete and cannot be accepted")
        if status != "corrected_pending_approval" and observation not in quote:
            raise HTTPException(
                status_code=409,
                detail="The extracted observation is not supported by its preserved source passage; no LCR assertion was created",
            )
        if not subject or not observation:
            raise HTTPException(status_code=422, detail="The candidate is incomplete and cannot be accepted")
        document_field = _document_assertion_field(candidate)
        asserted_value = _document_asserted_value(candidate, subject, observation)
        assertion_subject_type = "person"
        assertion_subject_id = person_id
        assertion_kind = "document_reviewed_observation"
        assertion_epistemic_state = "reported"
        review_after_days = 30
        matched_medication_id = candidate.get("matched_lcr_medication_id")
        if candidate.get("field_type") == "medication" and matched_medication_id:
            source_instruction = quote.casefold()
            medication_status = None
            if re.search(r"\b(discontinued|discontinue)\b", source_instruction):
                medication_status = "discontinued"
            elif re.search(r"\b(stopped|stop)\b", source_instruction):
                medication_status = "stopped"
            elif re.search(r"\b(held|hold)\b", source_instruction):
                medication_status = "held"
            if medication_status:
                document_field = "medication_status"
                asserted_value = medication_status
                assertion_subject_type = "medication"
                assertion_subject_id = str(matched_medication_id)
                assertion_kind = "current_state"
                assertion_epistemic_state = "reported"
                candidate_review_days = candidate.get("matched_lcr_medication_review_after_days")
                review_after_days = candidate_review_days if isinstance(candidate_review_days, int) and candidate_review_days > 0 else None
        duplicate_of_evidence_id = _duplicate_of(evidence)
        duplicate_label = " · duplicate copy; not independent corroboration" if duplicate_of_evidence_id else ""
        page_label = str(candidate.get("page_number") or "unresolved")
        source_prefix = "Caregiver-reviewed document (not independently verified): "
        source_suffix = f" · page {page_label}{duplicate_label}"
        filename = str(metadata.get("filename") or "Uploaded document")
        source_label = source_prefix + filename[:max(0, 160 - len(source_prefix) - len(source_suffix))] + source_suffix
        # Recover an assertion if a prior request committed the assertion but
        # failed before the candidate review state was committed.
        assertion_row = _existing_assertion_for_exact_document_duplicate(
            db,
            person_id=person_id,
            caregiver_id=caregiver_id,
            evidence_id=evidence_id,
            upload=upload,
            field=document_field,
            source_quote=quote,
            asserted_value=asserted_value,
            subject_type=assertion_subject_type,
            subject_id=assertion_subject_id,
            assertion_kind=assertion_kind,
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
                    subject_type=assertion_subject_type,
                    subject_id=assertion_subject_id,
                    field=document_field,
                    assertion_kind=assertion_kind,
                    asserted_value=asserted_value,
                    source_label=source_label,
                    source_text=quote,
                    evidence_ids=[evidence_id],
                    # A document candidate accepted by the caregiver remains
                    # a reported source, not a clinically verified instruction.
                    # For a matched medication status conflict, retain the
                    # existing medication's review window and subject identity.
                    review_after_days=review_after_days,
                    epistemic_state=assertion_epistemic_state,
                    event_time_text=candidate.get("event_date") or None,
                    created_by_caregiver_id=caregiver_id,
                    updated_by_caregiver_id=caregiver_id,
                ),
                commit=False,
            )
            assertion_row.assertion_status = "reported" if assertion_subject_type == "medication" and document_field == "medication_status" else "accepted"
            db.flush()
        else:
            assertion_row = db.query(CareAssertion).filter(
                CareAssertion.id == assertion_row.id,
                CareAssertion.person_id == person_id,
            ).with_for_update().first()
            if assertion_row is None:
                raise HTTPException(status_code=404, detail="Source-linked assertion not found in this care record")

        assertion_id = str(assertion_row.id)
        # Flush the assertion and its state revision in the same transaction as
        # the append-only review event below. No intermediate commit is allowed.
        db.flush()
        expected_assertion_status = "reported" if assertion_subject_type == "medication" and document_field == "medication_status" else "accepted"
        if assertion_row.assertion_status != expected_assertion_status:
            raise HTTPException(status_code=503, detail="The caregiver review was not applied to the Living Care Record")
        try:
            follow_up_id = _persist_document_follow_up(
                db,
                person_id=person_id,
                caregiver_id=caregiver_id,
                evidence_id=evidence_id,
                analysis_id=analysis_id,
                upload=upload,
                candidate=candidate,
                subject=subject,
                observation=observation,
                quote=quote,
            )
        except Exception as exc:
            db.rollback()
            logger.exception("Accepted document follow-up could not be persisted atomically")
            raise HTTPException(
                status_code=503,
                detail="The document review and its follow-up were not saved. Please retry the review.",
            ) from exc
        if follow_up_id:
            candidate["accepted_follow_up_id"] = follow_up_id
        candidate["review_status"] = "accepted_by_caregiver"
        candidate["accepted_assertion_id"] = assertion_id
        candidate["accepted_assertion_status"] = assertion_row.assertion_status
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
    change_intelligence_status = "not_required"
    execution = None
    if payload.decision == "accept":
        try:
            execution = execute_care_continuity_reconciliation(
                db,
                person_id,
                workflow="document_candidate_review",
                source_caregiver_id=caregiver_id,
                source_evidence_id=evidence_id,
                source_assertion_id=assertion_id,
            )
            change_intelligence_status = "available"
        except Exception as exc:
            db.rollback()
            logger.exception(
                "Shared care-continuity execution failed after document assertion review %s",
                assertion_id,
            )
            raise HTTPException(
                status_code=503,
                detail="Care-continuity reconciliation failed; the review was not committed. Retry the review.",
            ) from exc

    # Persist the assertion, append-only review event, candidate state and
    # reconciliation in one transaction for accepted document candidates.
    if payload.decision == "accept" and assertion_id:
        db.flush()
        persisted_assertion = db.query(CareAssertion.id).filter(
            CareAssertion.id == assertion_id,
            CareAssertion.person_id == person_id,
        ).first()
        if persisted_assertion is None:
            logger.error(
                "Accepted document assertion missing after reconciliation: assertion_id=%s person_id=%s "
                "object_person_id=%s object_status=%s same_person_assertions=%s transaction_active=%s",
                assertion_id,
                person_id,
                getattr(assertion_row, "person_id", None),
                getattr(assertion_row, "assertion_status", None),
                [
                    {
                        "id": row.id,
                        "kind": row.assertion_kind,
                        "status": row.assertion_status,
                        "evidence_ids": row.evidence_ids,
                    }
                    for row in db.query(CareAssertion).filter(CareAssertion.person_id == person_id).all()
                ],
                db.in_transaction(),
            )
            db.rollback()
            raise HTTPException(
                status_code=503,
                detail="The accepted document assertion was not retained by reconciliation. Retry the review.",
            )
    db.commit()
    if execution is not None:
        execution["stages"]["reconciliation"] = "committed"
    db.refresh(analysis)
    return {
        "analysis_id": analysis_id,
        "candidate": candidate,
        "review_decision": payload.decision,
        "source_evidence_id": evidence_id,
        "source_record_ref": {"record_type": "care_assertion", "record_id": assertion_id} if assertion_id else None,
        "written_to_lcr": bool(assertion_id),
        "assertion_status": assertion_row.assertion_status if assertion_id and assertion_row else None,
        "follow_up_record_ref": {"record_type": "open_loop", "record_id": follow_up_id} if follow_up_id else None,
        "review_required": payload.decision == "correct",
        "change_intelligence_status": change_intelligence_status,
        "care_continuity_execution": execution,
    }
