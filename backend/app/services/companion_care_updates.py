"""Persistence helpers for Companion proposals using the shared AI-output governance records."""
import json
import uuid

from sqlalchemy.orm import Session

from app.models.ai_output_governance import AIOutputLCRProposal


def proposal_payload(proposal: AIOutputLCRProposal) -> dict:
    change = proposal.proposed_change_json or {}
    return {
        "id": proposal.id,
        "proposal_type": proposal.proposal_type,
        "proposed_change": change,
        "status": proposal.status,
        "source_message_id": proposal.source_message_id,
        "source_conversation_id": proposal.source_conversation_id,
        "candidate_index": proposal.candidate_index,
        "proposal_version": proposal.proposal_version,
        "created_at": proposal.created_at.isoformat() if proposal.created_at else None,
    }


def persist_companion_candidates(
    db: Session,
    *,
    person_id: str,
    caregiver_id: str,
    conversation_id: str,
    message_id: str,
    source_text: str,
    extraction: dict,
) -> list[dict]:
    """Persist only exact-source-quoted proposals; never create a CareAssertion here."""
    output = []
    observations = extraction.get("observations") if isinstance(extraction, dict) else []
    for index, item in enumerate(observations or []):
        quote = item.get("source_quote") if isinstance(item, dict) else None
        if not isinstance(quote, str) or quote not in source_text:
            continue
        existing = db.query(AIOutputLCRProposal).filter(
            AIOutputLCRProposal.person_id == person_id,
            AIOutputLCRProposal.caregiver_id == caregiver_id,
            AIOutputLCRProposal.source_message_id == message_id,
            AIOutputLCRProposal.candidate_index == index,
            AIOutputLCRProposal.proposal_version == 1,
        ).first()
        if existing is not None:
            output.append(proposal_payload(existing))
            continue
        proposal = AIOutputLCRProposal(
            id=str(uuid.uuid4()),
            person_id=person_id,
            caregiver_id=caregiver_id,
            proposal_type="companion_care_observation",
            proposed_change_json={
                "subject_type": "person",
                "subject_id": person_id,
                "field": "care_observation",
                "assertion_kind": "time_bound_observation",
                "asserted_value": quote,
                "source_quote": quote,
                "source_message_id": message_id,
                "source_conversation_id": conversation_id,
                "information_status": "caregiver_reported_pending_review",
                "model_generated_paraphrase_used": False,
            },
            source_event_ids_json=[],
            source_evidence_ids_json=[],
            rationale="Qwen identified a possible care observation. The stored proposal is restricted to an exact quotation from the caregiver's original message.",
            uncertainty_json={"source_grounding": "exact_substring", "event_time": "not_supplied"},
            source_conversation_id=conversation_id,
            source_message_id=message_id,
            candidate_index=index,
            proposal_version=1,
            status="pending_authorization",
        )
        db.add(proposal)
        db.flush()
        output.append(proposal_payload(proposal))
    if output:
        db.commit()
    else:
        db.rollback()
    return output
