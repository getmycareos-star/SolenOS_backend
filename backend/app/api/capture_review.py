import hashlib
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.anonymous_session import AnonymousSession
from app.models.capture_analysis import CaptureAnalysis
from app.schemas.capture_review import CaptureCandidateReview

router = APIRouter(tags=["capture review"])


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
        .filter(CaptureAnalysis.id == analysis_id, CaptureAnalysis.person_id == person_id)
        .first()
    )
    if analysis is None:
        # Do not disclose whether another care record owns this analysis.
        raise HTTPException(status_code=404, detail="Capture analysis not found")
    if analysis.extraction_status != "available" or not isinstance(analysis.candidates, dict):
        raise HTTPException(status_code=409, detail="No reviewable extraction candidates are available")
    observations = analysis.candidates.get("observations")
    if not isinstance(observations, list) or payload.candidate_index >= len(observations):
        raise HTTPException(status_code=404, detail="Candidate not found")
    candidate = observations[payload.candidate_index]
    if not isinstance(candidate, dict):
        raise HTTPException(status_code=422, detail="Candidate data is invalid")
    if candidate.get("review_status", "pending_review") != "pending_review":
        raise HTTPException(status_code=409, detail="This candidate already has a review decision")

    original_candidate = dict(candidate)
    if payload.decision == "correct":
        if payload.corrected_subject is not None:
            candidate["subject"] = payload.corrected_subject.strip()
        if payload.corrected_observation is not None:
            candidate["observation"] = payload.corrected_observation.strip()
        candidate["review_status"] = "corrected_by_caregiver"
    elif payload.decision == "accept":
        candidate["review_status"] = "accepted_by_caregiver"
    else:
        candidate["review_status"] = "rejected_by_caregiver"

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
        "original_candidate": original_candidate,
        "reviewed_candidate": dict(candidate),
        "source_event_id": analysis.source_event_id,
        "source_evidence_id": analysis.source_evidence_id,
    })
    analysis.candidates = {**analysis.candidates, "observations": observations}
    analysis.review_history = history
    db.add(analysis)
    db.commit()
    db.refresh(analysis)

    return {
        "analysis_id": analysis.id,
        "candidate": candidate,
        "review_decision": payload.decision,
        "source_event_id": analysis.source_event_id,
        "source_evidence_id": analysis.source_evidence_id,
        "review_required": True,
        "written_to_lcr": False,
        "message": "Review decision saved with the original candidate preserved. The Living Care Record was not changed.",
    }
