import copy
import hashlib
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.anonymous_session import AnonymousSession
from app.models.capture_analysis import CaptureAnalysis
from app.models.care import CareEvent, Evidence as EvidenceModel
from app.schemas.capture_review import CaptureCandidateReview
from app.schemas.care_continuity import CareAssertionCreate
from app.services.care_continuity import create_assertion
from app.services.care_continuity_execution import execute_care_continuity_reconciliation
from app.services.lcr_context import build_lcr_context

router = APIRouter(tags=["capture review"])
logger = logging.getLogger(__name__)


def _session_scope(request: Request, db: Session) -> tuple[str, str]:
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


@router.get("/capture/analyses/{analysis_id}")
def get_capture_analysis(
    analysis_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Return only the authenticated caregiver's persisted extraction and review state."""
    person_id, caregiver_id = _session_scope(request, db)
    analysis = db.query(CaptureAnalysis).filter(
        CaptureAnalysis.id == analysis_id,
        CaptureAnalysis.person_id == person_id,
        CaptureAnalysis.caregiver_id == caregiver_id,
    ).first()
    if analysis is None:
        raise HTTPException(status_code=404, detail="Capture analysis not found")
    candidates = analysis.candidates if isinstance(analysis.candidates, dict) else {}
    observations = candidates.get("observations")
    observations = observations if isinstance(observations, list) else []
    reviewable = any(
        isinstance(item, dict)
        and item.get("review_status", "pending_review") in {
            "pending_review", "corrected_pending_approval", "corrected_by_caregiver",
        }
        for item in observations
    )
    return {
        "analysis_id": str(analysis.id),
        "extraction_status": analysis.extraction_status,
        "candidates": candidates,
        "execution_status": analysis.execution_status or {},
        "review_required": reviewable,
        "written_to_lcr": any(
            isinstance(item, dict) and item.get("review_status") == "accepted_by_caregiver"
            for item in observations
        ),
    }


@router.post("/capture/analyses/{analysis_id}/review")
def review_capture_candidate(
    analysis_id: str,
    payload: CaptureCandidateReview,
    request: Request,
    db: Session = Depends(get_db),
):
    person_id, caregiver_id = _session_scope(request, db)
    analysis = (
        db.query(CaptureAnalysis)
        .filter(
            CaptureAnalysis.id == analysis_id,
            CaptureAnalysis.person_id == person_id,
            CaptureAnalysis.caregiver_id == caregiver_id,
        )
        .with_for_update()
        .first()
    )
    if analysis is None:
        # Do not disclose whether another care record or caregiver owns this analysis.
        raise HTTPException(status_code=404, detail="Capture analysis not found")
    if analysis.extraction_status != "available" or not isinstance(analysis.candidates, dict):
        logger.warning(
            "Capture analysis review unavailable: analysis_id=%s extraction_status=%s candidates_type=%s",
            analysis.id,
            analysis.extraction_status,
            type(analysis.candidates).__name__,
        )
        raise HTTPException(status_code=409, detail="No reviewable extraction candidates are available")

    candidates_payload = copy.deepcopy(analysis.candidates)
    observations = candidates_payload.get("observations")
    if not isinstance(observations, list) or payload.candidate_index >= len(observations):
        raise HTTPException(status_code=404, detail="Candidate not found")
    candidate = observations[payload.candidate_index]
    if not isinstance(candidate, dict):
        raise HTTPException(status_code=422, detail="Candidate data is invalid")

    prior_status = candidate.get("review_status", "pending_review")
    reviewable_statuses = {"pending_review", "corrected_pending_approval", "corrected_by_caregiver"}
    if prior_status not in reviewable_statuses:
        raise HTTPException(status_code=409, detail="This candidate already has a final review decision")

    original_candidate = copy.deepcopy(candidate)
    reviewed_at = datetime.now(timezone.utc).isoformat()
    assertion_id = None

    if payload.decision == "correct":
        # Keep model/original candidate fields immutable. Caregiver edits are
        # stored in separate fields and become authoritative only on acceptance.
        if payload.corrected_subject is not None:
            candidate["corrected_subject"] = payload.corrected_subject.strip()
        if payload.corrected_observation is not None:
            candidate["corrected_observation"] = payload.corrected_observation.strip()
        candidate["review_status"] = "corrected_pending_approval"
    elif payload.decision == "reject":
        candidate["review_status"] = "rejected_by_caregiver"
    else:
        # Only Daily Care observations may enter the LCR through this workflow.
        # Revalidate both source references against the authenticated care record.
        if not analysis.source_event_id:
            raise HTTPException(status_code=409, detail="The candidate has no persisted Daily Care source event")
        source_event = db.query(CareEvent).filter(
            CareEvent.id == analysis.source_event_id,
            CareEvent.person_id == person_id,
            CareEvent.created_by_caregiver_id == caregiver_id,
            CareEvent.event_type.in_({
                "structured_daily_capture", "quick_daily_checkin", "daily_care_entry",
            }),
        ).with_for_update().first()
        if source_event is None:
            raise HTTPException(status_code=409, detail="The original Daily Care source could not be verified")

        if not analysis.source_evidence_id or analysis.source_evidence_id not in (source_event.evidence_ids or []):
            raise HTTPException(status_code=409, detail="The candidate is missing its original Daily Care evidence link")
        source_evidence = db.query(EvidenceModel).filter(
            EvidenceModel.id == analysis.source_evidence_id,
            EvidenceModel.person_id == person_id,
            EvidenceModel.reported_by_caregiver_id == caregiver_id,
        ).first()
        if source_evidence is None:
            raise HTTPException(status_code=409, detail="The Daily Care source evidence could not be verified")

        source_quote = candidate.get("source_quote")
        if (
            not isinstance(source_quote, str)
            or not source_quote.strip()
            or source_quote not in (source_evidence.source_text or "")
            or source_quote not in (source_event.description or "")
        ):
            raise HTTPException(
                status_code=409,
                detail="The candidate quotation does not match the preserved Daily Care source event and evidence; no LCR assertion was created",
            )

        subject = str(candidate.get("corrected_subject", candidate.get("subject")) or "").strip()
        observation = str(candidate.get("corrected_observation", candidate.get("observation")) or "").strip()
        if not observation:
            raise HTTPException(status_code=422, detail="The reviewed observation is empty")

        # Re-check provenance at the point of promotion, not only when Qwen
        # generated the candidate. The immutable source quote must still occur
        # verbatim in the persisted capture text. Uncorrected model wording must
        # be contained in that quote. A caregiver correction may differ, but is
        # recorded as a separate human-authored statement in the assertion trace.
        source_quote = candidate.get("source_quote")
        persisted_source = analysis.source_text or ""
        if (
            not isinstance(source_quote, str)
            or not source_quote.strip()
            or source_quote not in persisted_source
        ):
            raise HTTPException(
                status_code=409,
                detail="The candidate's original source passage could not be verified; no LCR assertion was created",
            )
        was_corrected = prior_status in {"corrected_pending_approval", "corrected_by_caregiver"}
        original_observation = str(candidate.get("observation") or "").strip()
        if not was_corrected and original_observation not in source_quote:
            raise HTTPException(
                status_code=409,
                detail="The extracted observation is not supported by its preserved source passage; no LCR assertion was created",
            )
        if was_corrected and observation != original_observation:
            candidate["accepted_correction"] = {
                "original_observation": original_observation,
                "caregiver_approved_observation": observation,
                "source_quote": source_quote,
                "correction_author": caregiver_id,
                "correction_approved_at": reviewed_at,
            }
        asserted_value = f"{subject}: {observation}" if subject else observation
        assertion = create_assertion(
            db,
            CareAssertionCreate(
                person_id=person_id,
                subject_type="person",
                subject_id=person_id,
                field=(subject or "daily_care_observation")[:120],
                assertion_kind="time_bound_observation",
                asserted_value=asserted_value[:4000],
                source_event_id=source_event.id,
                source_label="Caregiver-reported Daily Care observation; explicitly reviewed",
                source_text=source_evidence.source_text,
                evidence_ids=[source_evidence.id],
                observed_at=source_event.occurred_at,
                created_by_caregiver_id=caregiver_id,
            ),
            commit=False,
        )
        assertion.assertion_status = "caregiver_reviewed"
        assertion.updated_by_caregiver_id = caregiver_id
        db.flush()
        candidate["review_status"] = "accepted_by_caregiver"
        candidate["accepted_at"] = reviewed_at
        candidate["accepted_by_caregiver_id"] = caregiver_id
        assertion_id = assertion.id

    candidate["reviewed_at"] = reviewed_at
    candidate["reviewed_by_caregiver_id"] = caregiver_id
    candidate["review_decision"] = payload.decision

    history = list(analysis.review_history or [])
    history.append({
        "candidate_index": payload.candidate_index,
        "decision": payload.decision,
        "reviewed_at": reviewed_at,
        "reviewed_by_caregiver_id": caregiver_id,
        "original_candidate": original_candidate,
        "reviewed_candidate": copy.deepcopy(candidate),
        "source_event_id": analysis.source_event_id,
        "source_evidence_id": analysis.source_evidence_id,
        "assertion_id": assertion_id,
    })
    analysis.candidates = candidates_payload
    analysis.review_history = history
    remaining_reviewable = [
        item for item in observations
        if isinstance(item, dict)
        and item.get("review_status", "pending_review") in reviewable_statuses
    ]
    if analysis.execution_status is None:
        analysis.execution_status = {
            "source_preservation": "verified",
            "evidence": "verified" if analysis.source_evidence_id else "not_applicable",
            "source_message": "not_applicable",
            "extraction": "available",
            "assessment": "pending",
            "review": "required",
            "reconciliation": "not_attempted",
            "longitudinal_retrieval": "not_verified",
        }
    else:
        analysis.execution_status = dict(analysis.execution_status)
    analysis.execution_status["review"] = (
        "required" if remaining_reviewable else "completed"
    )
    db.add(analysis)

    change_intelligence_status = "not_required"
    execution = None
    if payload.decision == "accept":
        try:
            execution = execute_care_continuity_reconciliation(
                db,
                person_id,
                workflow="daily_capture_review",
                source_caregiver_id=caregiver_id,
                source_event_id=analysis.source_event_id,
                source_evidence_id=analysis.source_evidence_id,
                stage_statuses={
                    "extraction": "available",
                    "assessment": "pending",
                    "review": "required" if remaining_reviewable else "completed",
                },
            )
            execution["stages"]["review"] = (
                "required" if remaining_reviewable else "completed"
            )
            execution["stages"]["reconciliation"] = "completed"
            analysis.execution_status = dict(execution["stages"])
            change_intelligence_status = "available"
        except Exception as exc:
            db.rollback()
            raise HTTPException(
                status_code=503,
                detail="Care-continuity reconciliation failed; the review was not committed. Retry the review.",
            ) from exc

    # The accepted assertion, review decision, and reconciliation are committed
    # together. A failure cannot leave a caregiver-visible accepted review with
    # a silently stale longitudinal projection.
    db.commit()
    if execution is not None:
        execution["stages"]["reconciliation"] = "committed"
        try:
            projection = build_lcr_context(
                db,
                person_id,
                caregiver_id=caregiver_id,
                limit_per_source=1,
            )
            retrieval_status = (
                "available"
                if not projection.get("missing_required_sources")
                else "unavailable"
            )
        except Exception:
            retrieval_status = "unavailable"
        execution["stages"]["longitudinal_retrieval"] = retrieval_status
        analysis.execution_status = dict(execution["stages"])
        db.add(analysis)
        db.commit()
    db.refresh(analysis)

    return {
        "analysis_id": analysis.id,
        "candidate": candidate,
        "review_decision": payload.decision,
        "source_event_id": analysis.source_event_id,
        "source_evidence_id": analysis.source_evidence_id,
        "assertion_id": assertion_id,
        "review_required": payload.decision != "accept" and payload.decision != "reject",
        "change_intelligence_status": change_intelligence_status,
        "care_continuity_execution": execution,
        "written_to_lcr": payload.decision == "accept",
        "message": (
            "Caregiver-approved Daily Care observation saved as a source-linked LCR assertion."
            if payload.decision == "accept" and change_intelligence_status == "available"
            else "The LCR assertion was saved; derived change assessments need a separate retry."
            if payload.decision == "accept"
            else "Correction saved separately. The original observation is unchanged; the corrected candidate remains pending explicit approval."
            if payload.decision == "correct"
            else "Rejection recorded. No LCR assertion was created."
        ),
    }
