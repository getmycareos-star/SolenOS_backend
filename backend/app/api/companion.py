"""Companion query endpoints.

The trust-guarded conversational layer already exists in ConversationEngine, and
this module is a thin surface over it rather than a second implementation. The
single-question endpoint exists because a caregiver opening the app and asking
one question is not the same act as starting a conversation, and the difference
matters for grounding: a one-off question is held to the stricter standard and
refuses outright when nothing in the record supports an answer.
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.services.conversation import ConversationEngine

router = APIRouter(prefix="/companion", tags=["companion"])

NO_EVIDENCE_REPLY = "I don't see that in the record."


class CompanionQuery(BaseModel):
    person_id: str
    question: str
    caregiver_id: Optional[str] = None


class CompanionAnswer(BaseModel):
    person_id: str
    answer: str
    evidence_ids: List[str]
    confidence: Optional[float] = Field(None, ge=0.0, le=1.0)
    possible_context: Optional[str] = None
    # Set when the question could not be answered from the record. The caller
    # should show the refusal rather than treating an empty answer as a failure.
    grounded: bool
    guard_verdict: Optional[str] = None
    session_id: Optional[str] = None


def get_engine(db: Session = Depends(get_db)) -> ConversationEngine:
    return ConversationEngine(db)


@router.post("/query", response_model=CompanionAnswer)
def query(
    payload: CompanionQuery,
    engine: ConversationEngine = Depends(get_engine),
):
    """Answer one question from the record, or decline to.

    Delegates to the conversation engine so the guard, the evidence filter and
    the audit trail are the same ones every other reply goes through. A second
    pathway would mean a second set of trust rules to keep correct.
    """
    if not payload.question or not payload.question.strip():
        raise HTTPException(status_code=400, detail="A question is required.")

    try:
        result = engine.respond(
            person_id=payload.person_id,
            text=payload.question.strip(),
            caregiver_id=payload.caregiver_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    evidence_ids = result.get("evidence_ids") or []
    return CompanionAnswer(
        person_id=payload.person_id,
        answer=result.get("reply") or NO_EVIDENCE_REPLY,
        evidence_ids=list(evidence_ids),
        confidence=result.get("confidence"),
        possible_context=result.get("possible_context"),
        # A reply with no surviving citation is not an answer, whatever it says.
        # The guard already stripped the unsupported parts; this states the
        # outcome so a client cannot mistake prose for a finding.
        grounded=bool(evidence_ids),
        guard_verdict=result.get("guard_verdict"),
        session_id=result.get("session_id"),
    )


@router.post("/conversation", response_model=CompanionAnswer)
def conversation(
    payload: CompanionQuery,
    session_id: Optional[str] = None,
    engine: ConversationEngine = Depends(get_engine),
):
    """Continue a multi-turn thread, with the same guard on every turn.

    Each turn is stored with its own evidence ids, guard verdict and unresolved
    context, so a reply months later can be audited against the record as it
    stood when it was given.
    """
    if not payload.question or not payload.question.strip():
        raise HTTPException(status_code=400, detail="A question is required.")

    try:
        result = engine.respond(
            person_id=payload.person_id,
            text=payload.question.strip(),
            session_id=session_id,
            caregiver_id=payload.caregiver_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    evidence_ids = result.get("evidence_ids") or []
    return CompanionAnswer(
        person_id=payload.person_id,
        answer=result.get("reply") or NO_EVIDENCE_REPLY,
        evidence_ids=list(evidence_ids),
        confidence=result.get("confidence"),
        possible_context=result.get("possible_context"),
        grounded=bool(evidence_ids),
        guard_verdict=result.get("guard_verdict"),
        session_id=result.get("session_id"),
    )
