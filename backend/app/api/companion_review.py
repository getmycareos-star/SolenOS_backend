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
from app.models.care import CareEvent, Evidence
from app.models.qwen_background_job import QwenBackgroundJob, QwenModelOutput
from app.models.collaboration import ConversationMessage, ConversationSession
from app.models.care_continuity import CareAssertion
from app.schemas.care_continuity import CareAssertionCreate
from app.services.care_continuity import create_assertion
from app.services.care_continuity_execution import execute_care_continuity_reconciliation

router = APIRouter(prefix="/companion/care-updates", tags=["companion care updates"])


class CompanionCareUpdateReview(BaseModel):
    decision: str
    corrected_text: str | None = Field(default=None, min_length=1, max_length=4000)
    correction_context: str | None = Field(default=None, max_length=4000)


def _parse_explicit_event_time(value: str | None):
    """Parse only unambiguous timezone-aware ISO timestamps; retain other wording separately."""
    if not isinstance(value, str) or not value.strip():
        return None
    from datetime import datetime
    candidate = value.strip()
    try:
        parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


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


@router.post("/qwen/{proposal_id}/review")
def review_qwen_assistance_proposal(
    proposal_id: str,
    payload: CompanionCareUpdateReview,
    request: Request,
    db: Session = Depends(get_db),
):
    """Caregiver explicitly accepts or rejects a source-validated Qwen summary."""
    person_id, caregiver_id = _scope(request, db)
    decision = (payload.decision or "").strip().lower()
    if decision not in {"accept", "reject"}:
        raise HTTPException(status_code=422, detail="Decision must be accept or reject")
    proposal = db.query(AIOutputLCRProposal).filter(
        AIOutputLCRProposal.id == proposal_id,
        AIOutputLCRProposal.person_id == person_id,
        AIOutputLCRProposal.caregiver_id == caregiver_id,
        AIOutputLCRProposal.proposal_type == "qwen_assistance_summary",
    ).with_for_update().first()
    if proposal is None:
        raise HTTPException(status_code=404, detail="Care suggestion not found")

    prior = db.query(AIOutputLCRDecision).filter(
        AIOutputLCRDecision.proposal_id == proposal.id,
        AIOutputLCRDecision.person_id == person_id,
    ).order_by(AIOutputLCRDecision.created_at.asc()).all()
    terminal = "accepted" if decision == "accept" else "rejected"
    if proposal.status != "pending_authorization":
        if proposal.status == terminal and prior:
            assertion = db.query(CareAssertion).filter_by(source_proposal_id=proposal.id).first()
            return {
                "proposal_id": proposal.id,
                "status": proposal.status,
                "decision": decision,
                "assertion_id": assertion.id if assertion else None,
                "written_to_lcr": bool(assertion),
                "idempotent_replay": True,
            }
        raise HTTPException(status_code=409, detail="This care suggestion already has a review decision")

    output = db.query(QwenModelOutput).filter(
        QwenModelOutput.id == proposal.output_id,
        QwenModelOutput.job_id == proposal.job_id,
        QwenModelOutput.person_id == person_id,
        QwenModelOutput.caregiver_id == caregiver_id,
    ).with_for_update().first()
    job = db.query(QwenBackgroundJob).filter(
        QwenBackgroundJob.id == proposal.job_id,
        QwenBackgroundJob.person_id == person_id,
        QwenBackgroundJob.caregiver_id == caregiver_id,
        QwenBackgroundJob.status == "completed",
    ).first()
    if output is None or job is None or output.written_to_lcr:
        raise HTTPException(status_code=409, detail="The original care suggestion is no longer available")
    change = dict(proposal.proposed_change_json or {})
    if change.get("asserted_value") != output.output_text or change.get("information_status") != "ai_generated_interpretation_pending_caregiver_review":
        raise HTTPException(status_code=409, detail="The saved suggestion no longer matches its review proposal")

    event_ids = list(dict.fromkeys(str(value) for value in (proposal.source_event_ids_json or [])))
    evidence_ids = list(dict.fromkeys(str(value) for value in (proposal.source_evidence_ids_json or [])))
    event_rows = db.query(CareEvent.id).filter(
        CareEvent.person_id == person_id, CareEvent.id.in_(event_ids or ["__none__"])
    ).all()
    evidence_rows = db.query(Evidence.id).filter(
        Evidence.person_id == person_id, Evidence.id.in_(evidence_ids or ["__none__"])
    ).all()
    if {str(row[0]) for row in event_rows} != set(event_ids) or {str(row[0]) for row in evidence_rows} != set(evidence_ids):
        raise HTTPException(status_code=409, detail="One or more original care sources are no longer available")

    now = datetime.now(timezone.utc)
    assertion_id = None
    if decision == "reject":
        proposal.status = "rejected"
        output.review_status = "rejected"
        rationale = "Caregiver declined the Qwen-generated interpretation; no Living Care Record assertion was created."
    else:
        accepted_text = (change.get("asserted_value") or "").strip()
        if not accepted_text:
            raise HTTPException(status_code=409, detail="The care suggestion is empty")
        assertion = create_assertion(
            db,
            CareAssertionCreate(
                person_id=person_id,
                subject_type="person",
                subject_id=person_id,
                field="care_record_summary",
                assertion_kind="ai_assisted_summary",
                epistemic_state="inferred",
                asserted_value=accepted_text,
                source_event_id=event_ids[0] if len(event_ids) == 1 else None,
                source_label="Caregiver-approved Qwen summary",
                source_text=(
                    "AI-generated interpretation approved by the caregiver. "
                    "This is not independent clinical verification.\n"
                    f"Source event IDs: {', '.join(event_ids)}\n"
                    f"Source evidence IDs: {', '.join(evidence_ids)}\n"
                    f"Generated summary: {accepted_text}"
                )[:12000],
                evidence_ids=evidence_ids,
                created_by_caregiver_id=caregiver_id,
                updated_by_caregiver_id=caregiver_id,
                source_proposal_id=proposal.id,
            ),
            commit=False,
        )
        assertion.assertion_status = "caregiver_reviewed"
        assertion_id = assertion.id
        proposal.status = "accepted"
        output.review_status = "accepted"
        output.written_to_lcr = True
        rationale = "Caregiver explicitly approved this AI-generated interpretation. It remains classified as inferred, not verified."

    db.add(AIOutputLCRDecision(
        id=str(uuid.uuid4()),
        proposal_id=proposal.id,
        person_id=person_id,
        reviewer_caregiver_id=caregiver_id,
        decision=terminal,
        rationale=rationale,
        resulting_lcr_revision_id=assertion_id,
        created_at=now,
    ))
    db.add(proposal)
    db.add(output)
    db.commit()
    return {
        "proposal_id": proposal.id,
        "status": proposal.status,
        "decision": decision,
        "assertion_id": assertion_id,
        "written_to_lcr": decision == "accept",
        "epistemic_state": "inferred" if decision == "accept" else None,
        "idempotent_replay": False,
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


@router.get("/{proposal_id}/history")
def companion_care_update_history(
    proposal_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    person_id, caregiver_id = _scope(request, db)
    proposal = db.query(AIOutputLCRProposal).filter(
        AIOutputLCRProposal.id == proposal_id,
        AIOutputLCRProposal.person_id == person_id,
        AIOutputLCRProposal.caregiver_id == caregiver_id,
        AIOutputLCRProposal.proposal_type == "companion_care_observation",
    ).first()
    if proposal is None:
        raise HTTPException(status_code=404, detail="Care-update proposal not found")
    decisions = db.query(AIOutputLCRDecision).filter(
        AIOutputLCRDecision.proposal_id == proposal.id,
        AIOutputLCRDecision.person_id == person_id,
    ).order_by(AIOutputLCRDecision.created_at.asc(), AIOutputLCRDecision.id.asc()).all()
    return {
        "proposal": _proposal_json(proposal),
        "items": [{
            "id": item.id,
            "decision": item.decision,
            "reviewer_caregiver_id": item.reviewer_caregiver_id,
            "rationale": item.rationale,
            "correction_context": item.clarification_request,
            "resulting_lcr_revision_id": item.resulting_lcr_revision_id,
            "created_at": item.created_at.isoformat() if item.created_at else None,
        } for item in decisions],
        "append_only": True,
    }


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
        terminal_status = {"accept": "accepted", "correct": "corrected", "reject": "rejected"}[decision]
        if proposal.status == terminal_status and existing_decisions:
            if decision == "correct":
                corrected_value = (payload.corrected_text or "").strip()
                next_version = int(proposal.proposal_version or 1) + 1
                corrected_proposal = db.query(AIOutputLCRProposal).filter(
                    AIOutputLCRProposal.person_id == person_id,
                    AIOutputLCRProposal.caregiver_id == caregiver_id,
                    AIOutputLCRProposal.proposal_type == "companion_care_observation",
                    AIOutputLCRProposal.source_message_id == proposal.source_message_id,
                    AIOutputLCRProposal.candidate_index == proposal.candidate_index,
                    AIOutputLCRProposal.proposal_version == next_version,
                ).first()
                if corrected_proposal is None or (corrected_proposal.proposed_change_json or {}).get("asserted_value") != corrected_value:
                    raise HTTPException(status_code=409, detail="This proposal already has a different correction decision")
                return {
                    "proposal": _proposal_json(proposal),
                    "corrected_proposal": _proposal_json(corrected_proposal),
                    "decision": decision,
                    "idempotent_replay": True,
                }
            assertion = db.query(CareAssertion).filter_by(source_proposal_id=proposal.id).first()
            active_assertion = assertion is not None and assertion.assertion_status not in {"rejected", "superseded"}
            return {
                "proposal": _proposal_json(proposal),
                "decision": decision,
                "assertion_id": assertion.id if active_assertion else None,
                "written_to_lcr": active_assertion,
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
            AIOutputLCRProposal.person_id == person_id,
            AIOutputLCRProposal.caregiver_id == caregiver_id,
            AIOutputLCRProposal.proposal_type == "companion_care_observation",
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
            assertion = create_assertion(
                db,
                CareAssertionCreate(
                    person_id=person_id, subject_type="person", subject_id=person_id,
                    field="care_observation", assertion_kind="time_bound_observation",
                    asserted_value=accepted_value,
                    source_label="Caregiver-reviewed Companion conversation",
                    source_text=source_quote + (("\nCaregiver-supplied correction context: " + correction_context) if correction_context else ""),
                    source_conversation_id=session.id, source_message_id=source_message.id,
                    source_proposal_id=proposal.id, event_time_text=original.get("event_time_text"),
                    evidence_ids=[],
                    observed_at=_parse_explicit_event_time(original.get("event_time_text")),
                    created_by_caregiver_id=caregiver_id,
                    updated_by_caregiver_id=caregiver_id,
                ),
                commit=False,
            )
            assertion.assertion_status = "caregiver_reviewed"
            assertion_id = assertion.id
        else:
            existing_assertion.assertion_status = "caregiver_reviewed"
            existing_assertion.asserted_value = accepted_value
            existing_assertion.source_label = "Caregiver-reviewed Companion conversation"
            existing_assertion.source_text = source_quote + (("\nCaregiver-supplied correction context: " + correction_context) if correction_context else "")
            existing_assertion.observed_at = _parse_explicit_event_time(original.get("event_time_text"))
            existing_assertion.event_time_text = original.get("event_time_text")
            existing_assertion.updated_by_caregiver_id = caregiver_id
            assertion_id = existing_assertion.id
        proposal.status = "accepted"
        db.add(AIOutputLCRDecision(
            id=str(uuid.uuid4()), proposal_id=proposal.id, person_id=person_id,
            reviewer_caregiver_id=caregiver_id, decision="accepted",
            rationale="Caregiver explicitly accepted this source-attributed report into the LCR; acceptance is not independent clinical verification.",
            resulting_lcr_revision_id=assertion_id, created_at=now,
        ))

    db.add(proposal)
    change_intelligence_status = "not_required"
    execution = None
    if decision == "accept":
        # The original user message is the persisted Companion source. Verify
        # its conversation/person scope before reconciling the accepted claim.
        try:
            execution = execute_care_continuity_reconciliation(
                db,
                person_id,
                workflow="companion_care_update_review",
                source_caregiver_id=caregiver_id,
                source_message_id=source_message.id,
                source_conversation_id=session.id,
            )
            change_intelligence_status = "available"
        except Exception as exc:
            db.rollback()
            raise HTTPException(
                status_code=503,
                detail="Care-continuity reconciliation failed; the Companion review was not committed. Retry the review.",
            ) from exc
    db.commit()
    if execution is not None:
        execution["stages"]["reconciliation"] = "committed"
    db.refresh(proposal)
    if corrected_proposal is not None:
        db.refresh(corrected_proposal)
    return {
        "proposal": _proposal_json(proposal),
        "corrected_proposal": _proposal_json(corrected_proposal) if corrected_proposal else None,
        "decision": decision,
        "assertion_id": assertion_id,
        "written_to_lcr": bool(assertion_id),
        "change_intelligence_status": change_intelligence_status,
        "care_continuity_execution": execution,
        "idempotent_replay": False,
    }
