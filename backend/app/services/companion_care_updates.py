"""Persistence helpers for Companion proposals using the shared AI-output governance records."""
import json
import hashlib
import logging
import uuid

from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.models.ai_output_governance import AIOutputLCRProposal
from app.models.anonymous_session import AnonymousSession
from app.models.caregiver_access import CaregiverAccessGrant
from app.models.collaboration import ConversationMessage, ConversationSession
from datetime import datetime, timezone
from app.services.caregiver_permissions import caregiver_has_permission
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
            AIOutputLCRProposal.proposal_type == "companion_care_observation",
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
            uncertainty_json={
                "source_grounding": "exact_substring",
                "event_time": "exact_quoted_phrase" if event_time_quote else "not_supplied",
                "clinical_status": "not_assessed",
                "interpretation": "not_inferred",
                "caregiver_review": "required",
            },
            source_conversation_id=conversation_id,
            source_message_id=message_id,
            candidate_index=index,
            proposal_version=1,
            status="pending_authorization",
        )
        try:
            with db.begin_nested():
                db.add(proposal)
                db.flush()
            output.append(proposal_payload(proposal))
        except IntegrityError:
            existing = db.query(AIOutputLCRProposal).filter(
                AIOutputLCRProposal.person_id == person_id,
                AIOutputLCRProposal.caregiver_id == caregiver_id,
                AIOutputLCRProposal.proposal_type == "companion_care_observation",
                AIOutputLCRProposal.source_message_id == message_id,
                AIOutputLCRProposal.candidate_index == index,
                AIOutputLCRProposal.proposal_version == 1,
            ).first()
            if existing is None:
                raise
            output.append(proposal_payload(existing))
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
    """Extract exact-quote proposals after persistence, under a durable message lease.

    A separate session is used so model latency cannot hold the Companion
    request's PostgreSQL connection or conversation lease. The expiring
    extraction lease makes retries safe if a response was lost or a worker
    restarted while the model was running.
    """
    session_factory = sessionmaker(bind=bind, expire_on_commit=False)
    db = session_factory()
    owner_token = str(uuid.uuid4())
    lock_key = "care-update:" + hashlib.sha256(str(message_id).encode("utf-8")).hexdigest()
    leased = False
    try:
        if db.get_bind().dialect.name == "postgresql":
            acquired = db.execute(
                text("""
                INSERT INTO companion_turn_leases
                    (lock_key, owner_token, expires_at, updated_at)
                VALUES
                    (:lock_key, :owner_token, now() + interval '360 seconds', now())
                ON CONFLICT (lock_key) DO UPDATE SET
                    owner_token = EXCLUDED.owner_token,
                    expires_at = EXCLUDED.expires_at,
                    updated_at = EXCLUDED.updated_at
                WHERE companion_turn_leases.expires_at <= now()
                RETURNING lock_key
                """),
                {"lock_key": lock_key, "owner_token": owner_token},
            ).first()
            db.commit()
            if acquired is None:
                return
            leased = True

        auth_session = db.query(AnonymousSession).filter(
            AnonymousSession.id == anonymous_session_id,
            AnonymousSession.person_id == person_id,
            AnonymousSession.caregiver_id == caregiver_id,
            AnonymousSession.revoked_at.is_(None),
            AnonymousSession.expires_at > datetime.now(timezone.utc),
        ).first()
        conversation = db.query(ConversationSession).filter_by(
            id=conversation_id, person_id=person_id, caregiver_id=caregiver_id,
            status="active",
        ).first()
        grant = db.query(CaregiverAccessGrant).filter_by(
            person_id=person_id, caregiver_id=caregiver_id, status="active",
        ).first()
        original_message = db.query(ConversationMessage).filter_by(
            id=message_id, session_id=conversation_id, role="user",
        ).first()
        if (
            auth_session is None
            or conversation is None
            or grant is None
            or not caregiver_has_permission(grant, "view_conversations")
            or original_message is None
            or original_message.content != source_text
        ):
            db.rollback()
            return

        existing = db.query(AIOutputLCRProposal.id).filter(
            AIOutputLCRProposal.person_id == person_id,
            AIOutputLCRProposal.caregiver_id == caregiver_id,
            AIOutputLCRProposal.proposal_type == "companion_care_observation",
            AIOutputLCRProposal.source_message_id == message_id,
            AIOutputLCRProposal.proposal_version == 1,
        ).first()
        if existing is not None:
            db.rollback()
            return

        # Release the read transaction and its connection before model inference.
        db.rollback()
        extraction = extract_companion_care_candidates(
            source_text,
            audit_context={
                "person_id": person_id,
                "caregiver_id": caregiver_id,
                "conversation_session_id": conversation_id,
                "request_id": message_id,
            },
        )
        # Recheck the grant after inference and hold its row lock through the
        # proposal commit. A revoked caregiver cannot leave a late proposal.
        current_grant = db.query(CaregiverAccessGrant).filter_by(
            person_id=person_id, caregiver_id=caregiver_id, status="active",
        ).with_for_update().first()
        if current_grant is None or not caregiver_has_permission(current_grant, "view_conversations"):
            db.rollback()
            return
        current_session = db.query(AnonymousSession).filter(
            AnonymousSession.id == anonymous_session_id,
            AnonymousSession.person_id == person_id,
            AnonymousSession.caregiver_id == caregiver_id,
            AnonymousSession.revoked_at.is_(None),
            AnonymousSession.expires_at > datetime.now(timezone.utc),
        ).with_for_update().first()
        current_conversation = db.query(ConversationSession).filter_by(
            id=conversation_id, person_id=person_id, caregiver_id=caregiver_id,
            status="active",
        ).with_for_update().first()
        if current_session is None or current_conversation is None:
            db.rollback()
            return
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
        if leased:
            try:
                db.rollback()
                db.execute(
                    text("""
                    DELETE FROM companion_turn_leases
                    WHERE lock_key = :lock_key AND owner_token = :owner_token
                    """),
                    {"lock_key": lock_key, "owner_token": owner_token},
                )
                db.commit()
            except Exception:
                db.rollback()
                logger.exception("Companion care-update lease cleanup failed; expiry will recover it")
        db.close()
