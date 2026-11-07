"""Caregiver review endpoints for Companion proposals stored in shared AI governance records."""
import json
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.collaboration import _scope
from app.core.database import get_db
from app.models.ai_output_governance import AIOutputLCRDecision, AIOutputLCRProposal
from app.models.collaboration import ConversationMessage, ConversationSession
from app.models.care_continuity import CareAssertion

router = APIRouter(prefix="/companion/care-updates", tags=["companion care updates"])


class CompanionCareUpdateReview(BaseModel):
    decision: str
    corrected_text: str | None = Field(default=None, min_length=1, max_length=4000)
    correction_context: str | None = Field(default=None, max_length=4000)


def _proposal_json(proposal: AIOutputLCRProposal) -> dict:
    return {
        "id": proposal.id,
        "status": proposal.status,
        "proposal_type": proposal.proposal_type,
        "proposed_change": proposal.proposed_change_json or {},
        "source_message_id": proposal.source_message_id,
        "source_conversation_id": proposal.source_conversation_id,
        "candidate_index": proposal.candidate_index,
        "proposal_version": proposal.proposal_version,
        "created_at": proposal.created_at.isoformat() if proposal.created_at else None,
    }


@router.get("")
def list_companion_care_updates(
    request: Request,
    session_id: str | None = None,
    db: Session = Depends(get_db),
):
    person_id, caregiver_id = _scope(request, db)
    query = db.query(AIOutputLCRProposal).filter(
        AIOutputLCRProposal.person_id == person_id,
        AIOutputLCRProposal.caregiver_id == caregiver_id,
        AIOutputLCRProposal.proposal_type == "companion_care_observation",
        AIOutputLCRProposal.status == "pending_authorization",
    )
    if session_id:
        session = db.query(ConversationSession).filter_by(
            id=session_id, person_id=person_id, caregiver_id=caregiver_id
        ).first()
        if session is None:
            raise HTTPException(status_code=404, detail="Conversation not found")
        query = query.filter(AIOutputLCRProposal.source_conversation_id == session.id)
    proposals = query.order_by(AIOutputLCRProposal.created_at.asc()).limit(100).all()
    return {"items": [_proposal_json(item) for item in proposals]}


@router.post("/{proposal_id}/review")
def review_companion_care_update(
    proposal_id: str,
    payload: CompanionCareUpdateReview,
    request: Request,
    db: Session = Depends(get_db),
):
    person_id, caregiver_id = _scope(request, db)
    decision = (payload.decision or "").strip().lower()
    if decision not in {"accept", "correct", "reject"}:
        raise HTTPException(status_code=422, detail="Decision must be accept, correct, or reject")

    proposal = db.query(AIOutputLCRProposal).filter(
        AIOutputLCRProposal.id == proposal_id,
        AIOutputLCRProposal.person_id == person_id,
        AIOutputLCRProposal.caregiver_id == caregiver_id,
        AIOutputLCRProposal.proposal_type == "companion_care_observation",
    ).with_for_update().first()
    if proposal is None:
        raise HTTPException(status_code=404, detail="Care-update proposal not found")

    existing_decisions = db.query(AIOutputLCRDecision).filter(
        AIOutputLCRDecision.proposal_id == proposal.id,
        AIOutputLCRDecision.person_id == person_id,
    ).order_by(AIOutputLCRDecision.created_at.asc()).all()
    if proposal.status != "pending_authorization":
        if existing_decisions and existing_decisions[-1].decision == decision:
            assertion = db.query(CareAssertion).filter_by(source_proposal_id=proposal.id).first()
            return {
                "proposal": _proposal_json(proposal),
                "decision": decision,
                "assertion_id": assertion.id if assertion else None,
                "idempotent_replay": True,
            }
        raise HTTPException(status_code=409, detail="This proposal already has a review decision")

    session = db.query(ConversationSession).filter(
        ConversationSession.id == proposal.source_conversation_id,
        ConversationSession.person_id == person_id,
        ConversationSession.caregiver_id == caregiver_id,
    ).first()
    source_message = db.query(ConversationMessage).filter(
        ConversationMessage.id == proposal.source_message_id,
        ConversationMessage.session_id == proposal.source_conversation_id,
        ConversationMessage.role == "user",
    ).first()
    if session is None or source_message is None:
        raise HTTPException(status_code=409, detail="The original Companion source message is no longer available")

    original = dict(proposal.proposed_change_json or {})
    source_quote = original.get("source_quote")
    if not isinstance(source_quote, str) or source_quote not in source_message.content:
        raise HTTPException(status_code=409, detail="The proposal no longer has valid source grounding")

    now = datetime.now(timezone.utc)
    assertion_id = None
    corrected_proposal = None

    if decision == "reject":
        proposal.status = "rejected"
        db.add(AIOutputLCRDecision(
            id=str(uuid.uuid4()), proposal_id=proposal.id, person_id=person_id,
            reviewer_caregiver_id=caregiver_id, decision="rejected",
            rationale="Caregiver rejected the proposed Companion care update.",
            created_at=now,
        ))
    elif decision == "correct":
        corrected = (payload.corrected_text or "").strip()
        context = (payload.correction_context or "").strip()
        if not corrected:
            raise HTTPException(status_code=422, detail="A corrected statement is required")
        if corrected not in source_message.content and (not context or corrected not in context):
            raise HTTPException(
                status_code=422,
                detail="The correction must be grounded in the original message or an explicitly supplied correction context.",
            )
        next_version = int(proposal.proposal_version or 1) + 1
        existing_corrected = db.query(AIOutputLCRProposal).filter(
            AIOutputLCRProposal.source_message_id == proposal.source_message_id,
            AIOutputLCRProposal.candidate_index == proposal.candidate_index,
            AIOutputLCRProposal.proposal_version == next_version,
        ).first()
        if existing_corrected:
            return {"proposal": _proposal_json(proposal), "corrected_proposal": _proposal_json(existing_corrected), "decision": "correct", "idempotent_replay": True}
        proposal.status = "corrected"
        corrected_proposal = AIOutputLCRProposal(
            id=str(uuid.uuid4()), person_id=person_id, caregiver_id=caregiver_id,
            proposal_type="companion_care_observation",
            proposed_change_json={
                **original,
                "asserted_value": corrected,
                "correction_text": corrected,
                "correction_context": context or None,
                "source_quote": source_quote,
                "information_status": "caregiver_corrected_pending_review",
                "supersedes_proposal_id": proposal.id,
            },
            source_event_ids_json=list(proposal.source_event_ids_json or []),
            source_evidence_ids_json=list(proposal.source_evidence_ids_json or []),
            rationale="Caregiver correction stored separately. It remains pending until a separate explicit acceptance.",
            uncertainty_json={**(proposal.uncertainty_json or {}), "correction_grounding": "original_message_or_explicit_context"},
            source_conversation_id=proposal.source_conversation_id,
            source_message_id=proposal.source_message_id,
            candidate_index=proposal.candidate_index,
            proposal_version=next_version,
            status="pending_authorization",
        )
        db.add(corrected_proposal)
        db.add(AIOutputLCRDecision(
            id=str(uuid.uuid4()), proposal_id=proposal.id, person_id=person_id,
            reviewer_caregiver_id=caregiver_id, decision="corrected",
            rationale=corrected, clarification_request=context or None, created_at=now,
        ))
    else:
        accepted_value = (original.get("asserted_value") or "").strip()
        correction_context = (original.get("correction_context") or "").strip()
        if not accepted_value or (
            accepted_value not in source_message.content
            and (not correction_context or accepted_value not in correction_context)
        ):
            raise HTTPException(status_code=422, detail="The accepted statement is not grounded in the original message or recorded correction context")
        existing_assertion = db.query(CareAssertion).filter_by(source_proposal_id=proposal.id).first()
        if existing_assertion is None:
            assertion = CareAssertion(
                id=str(uuid.uuid4()), person_id=person_id, subject_type="person",
                subject_id=person_id, field="care_observation",
                assertion_kind="time_bound_observation", asserted_value=accepted_value,
                source_label="Caregiver-reviewed Companion conversation",
                source_text=(source_quote + (("\nCaregiver-supplied correction context: " + correction_context) if correction_context else "")),
                source_conversation_id=session.id,
                source_message_id=source_message.id,
                source_proposal_id=proposal.id,
                evidence_ids=[], assertion_status="reported",
                observed_at=None, created_by_caregiver_id=caregiver_id,
            )
            db.add(assertion)
            db.flush()
            assertion_id = assertion.id
        else:
            assertion_id = existing_assertion.id
        proposal.status = "accepted"
        db.add(AIOutputLCRDecision(
            id=str(uuid.uuid4()), proposal_id=proposal.id, person_id=person_id,
            reviewer_caregiver_id=caregiver_id, decision="accepted",
            rationale="Caregiver explicitly accepted this source-attributed report into the LCR; acceptance is not independent clinical verification.",
            resulting_lcr_revision_id=assertion_id, created_at=now,
        ))

    db.add(proposal)
    db.commit()
    db.refresh(proposal)
    if corrected_proposal is not None:
        db.refresh(corrected_proposal)
    return {
        "proposal": _proposal_json(proposal),
        "corrected_proposal": _proposal_json(corrected_proposal) if corrected_proposal else None,
        "decision": decision,
        "assertion_id": assertion_id,
        "written_to_lcr": bool(assertion_id),
        "idempotent_replay": False,
    }
