"""Persistence helpers for Companion proposals using the shared AI-output governance records."""
import json
import logging
import uuid

from sqlalchemy.orm import sessionmaker

from sqlalchemy.orm import Session

from app.models.ai_output_governance import AIOutputLCRProposal
from app.models.anonymous_session import AnonymousSession
from app.models.collaboration import ConversationMessage, ConversationSession
from datetime import datetime, timezone
from app.services.qwen_gateway import extract_companion_care_candidates

logger = logging.getLogger(__name__)


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
        if not isinstance(quote, str) or len(quote.strip()) < 8 or quote not in source_text:
            continue
        event_time_quote = item.get("event_time_quote") if isinstance(item.get("event_time_quote"), str) else None
        event_time_quote = event_time_quote.strip() if event_time_quote else None
        if event_time_quote and (event_time_quote not in quote or event_time_quote not in source_text):
            event_time_quote = None
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
                "event_time_text": event_time_quote,
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



def process_companion_care_update_message(
    bind,
    *,
    person_id: str,
    caregiver_id: str,
    conversation_id: str,
    message_id: str,
    source_text: str,
    anonymous_session_id: str,
) -> None:
    """Run candidate extraction after the chat response has been sent.

    A separate session is created from the request's database bind. Model
    latency and failures therefore cannot hold the Companion request open or
    roll back the already-committed conversation pair.
    """
    session_factory = sessionmaker(bind=bind, expire_on_commit=False)
    db = session_factory()
    try:
        auth_session = db.query(AnonymousSession).filter(
            AnonymousSession.id == anonymous_session_id,
            AnonymousSession.person_id == person_id,
            AnonymousSession.caregiver_id == caregiver_id,
            AnonymousSession.revoked_at.is_(None),
            AnonymousSession.expires_at > datetime.now(timezone.utc),
        ).first()
        conversation = db.query(ConversationSession).filter_by(
            id=conversation_id, person_id=person_id, caregiver_id=caregiver_id,
        ).first()
        original_message = db.query(ConversationMessage).filter_by(
            id=message_id, session_id=conversation_id, role="user",
        ).first()
        if auth_session is None or conversation is None or original_message is None or original_message.content != source_text:
            return
        extraction = extract_companion_care_candidates(source_text)
        persist_companion_candidates(
            db,
            person_id=person_id,
            caregiver_id=caregiver_id,
            conversation_id=conversation_id,
            message_id=message_id,
            source_text=source_text,
            extraction=extraction,
        )
    except Exception:
        db.rollback()
        logger.exception("Companion care-update identification failed after message persistence")
    finally:
        db.close()
