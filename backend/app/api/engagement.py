from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional

from app.core.database import get_db
from app.schemas.care_context import (
    ConversationMessage,
    ConversationResponse,
    EngagementOpportunityResponse,
    FollowUpComplete,
    FollowUpCreate,
    FollowUpReschedule,
    FollowUpResponse,
)
from app.services.conversation import ConversationEngine
from app.services.follow_up import FollowUpEngine
from app.services.proactive import ProactiveEngine

router = APIRouter(prefix="/follow-up", tags=["follow-up"])


def get_engine(db: Session = Depends(get_db)) -> FollowUpEngine:
    return FollowUpEngine(db)


@router.post("/schedule", response_model=FollowUpResponse)
def schedule(payload: FollowUpCreate, engine: FollowUpEngine = Depends(get_engine)):
    try:
        follow_up = engine.schedule(
            person_id=payload.person_id,
            what_to_recheck=payload.what_to_recheck,
            trigger=payload.trigger,
            interval_days=payload.interval_days,
            due_at=payload.due_at,
            why_open=payload.why_open,
            what_result_changes_the_plan=payload.what_result_changes_the_plan,
            subject_type=payload.subject_type,
            subject_id=payload.subject_id,
            field=payload.field,
            evidence_ids=payload.evidence_ids,
            cadence=payload.cadence,
            priority_hint=payload.priority_hint,
        )
        engine.db.commit()
        return follow_up
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/sweep", response_model=List[FollowUpResponse])
def sweep(person_id: str, engine: FollowUpEngine = Depends(get_engine)):
    """Find what is open and put re-checks on it, without duplicating."""
    created = engine.schedule_from_open_state(person_id)
    engine.db.commit()
    return created


@router.get("/{person_id}/view")
def schedule_view(person_id: str, engine: FollowUpEngine = Depends(get_engine)):
    return engine.schedule_view(person_id)


@router.get("/{person_id}/due", response_model=List[FollowUpResponse])
def due(
    person_id: str,
    grace_days: int = 0,
    engine: FollowUpEngine = Depends(get_engine),
):
    return engine.due(person_id, grace_days=grace_days)


@router.post("/rationale")
def rationale(
    person_id: str,
    what_to_recheck: str,
    engine: FollowUpEngine = Depends(get_engine),
):
    return engine.rationale_for(person_id, what_to_recheck)


@router.post("/{follow_up_id}/complete", response_model=FollowUpResponse)
def complete(
    follow_up_id: str,
    payload: FollowUpComplete,
    engine: FollowUpEngine = Depends(get_engine),
):
    try:
        follow_up = engine.complete(follow_up_id, payload.outcome, payload.caregiver_id)
        engine.db.commit()
        return follow_up
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/{follow_up_id}/reschedule", response_model=FollowUpResponse)
def reschedule(
    follow_up_id: str,
    payload: FollowUpReschedule,
    engine: FollowUpEngine = Depends(get_engine),
):
    try:
        follow_up = engine.reschedule(follow_up_id, payload.due_at, payload.why)
        engine.db.commit()
        return follow_up
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/{follow_up_id}/cancel", response_model=FollowUpResponse)
def cancel(
    follow_up_id: str,
    reason: str,
    engine: FollowUpEngine = Depends(get_engine),
):
    try:
        follow_up = engine.cancel(follow_up_id, reason)
        engine.db.commit()
        return follow_up
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# ----------------------------------------------------------------------
# Proactive engagement
# ----------------------------------------------------------------------

proactive_router = APIRouter(prefix="/proactive", tags=["proactive"])


def get_proactive_engine(db: Session = Depends(get_db)) -> ProactiveEngine:
    return ProactiveEngine(db)


@proactive_router.post("/evaluate")
def evaluate(
    person_id: str,
    caregiver_local_hour: Optional[int] = None,
    engine: ProactiveEngine = Depends(get_proactive_engine),
):
    """Decide whether to reach out. Nothing is sent by this call."""
    return engine.evaluate(person_id, caregiver_local_hour=caregiver_local_hour)


@proactive_router.get("/{person_id}/opportunities", response_model=List[EngagementOpportunityResponse])
def list_opportunities(
    person_id: str, engine: ProactiveEngine = Depends(get_proactive_engine)
):
    return engine.list_opportunities(person_id)


@proactive_router.get("/{person_id}/pending", response_model=List[EngagementOpportunityResponse])
def pending(person_id: str, engine: ProactiveEngine = Depends(get_proactive_engine)):
    return engine.pending(person_id)


@proactive_router.post("/opportunity/{opportunity_id}/act-on", response_model=EngagementOpportunityResponse)
def act_on(
    opportunity_id: str, engine: ProactiveEngine = Depends(get_proactive_engine)
):
    try:
        opportunity = engine.mark_acted_on(opportunity_id)
        engine.db.commit()
        return opportunity
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# ----------------------------------------------------------------------
# Conversation
# ----------------------------------------------------------------------

conversation_router = APIRouter(prefix="/conversation", tags=["conversation"])


def get_conversation_engine(db: Session = Depends(get_db)) -> ConversationEngine:
    return ConversationEngine(db)


@conversation_router.post("/message", response_model=ConversationResponse)
def message(
    payload: ConversationMessage, engine: ConversationEngine = Depends(get_conversation_engine)
):
    try:
        return engine.respond(
            person_id=payload.person_id,
            text=payload.text,
            session_id=payload.session_id,
            caregiver_id=payload.caregiver_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@conversation_router.get("/session/{session_id}")
def session_view(session_id: str, engine: ConversationEngine = Depends(get_conversation_engine)):
    try:
        return engine.session_view(session_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
