from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional

from app.core.database import get_db
from app.schemas.care_context import (
    BehaviorHypothesisReject,
    BehaviorInterpretationResponse,
    BehaviorObservationCreate,
    PersonAttributeCreate,
    PersonAttributeResponse,
    PersonCalmerCreate,
    PersonCalmerResponse,
    PersonProfileResponse,
    PersonTriggerCreate,
    PersonTriggerResponse,
)
from app.services.behavior import BehaviorEngine
from app.services.person_model import PersonModelEngine
from app.core.cache_decorators import cached_response

router = APIRouter(prefix="/person", tags=["person"])


def get_engine(db: Session = Depends(get_db)) -> PersonModelEngine:
    return PersonModelEngine(db)


@cached_response(ttl=300, key_prefix="person_profile")
@router.get("/{person_id}/profile", response_model=PersonProfileResponse)
def get_profile(person_id: str, engine: PersonModelEngine = Depends(get_engine)):
    profile = engine.get_profile(person_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Person profile not found")
    return profile


@router.post("/profile/build", response_model=PersonProfileResponse)
def build_profile(person_id: str, engine: PersonModelEngine = Depends(get_engine)):
    return engine.build_profile(person_id)


@cached_response(ttl=300, key_prefix="person_describe")
@router.get("/{person_id}/describe")
def describe(person_id: str, engine: PersonModelEngine = Depends(get_engine)):
    return engine.describe(person_id)


@router.post("/attributes", response_model=PersonAttributeResponse)
def add_attribute(payload: PersonAttributeCreate, engine: PersonModelEngine = Depends(get_engine)):
    try:
        attribute = engine.add_attribute(
            person_id=payload.person_id,
            domain=payload.domain,
            label=payload.label,
            value=payload.value,
            evidence_ids=payload.evidence_ids,
            source_quote=payload.source_quote,
            stated_by_caregiver_id=payload.stated_by_caregiver_id,
            confidence=payload.confidence,
            possible_context=payload.possible_context,
        )
        engine.db.commit()
        return attribute
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@cached_response(ttl=300, key_prefix="person_attributes")
@router.get("/{person_id}/attributes", response_model=List[PersonAttributeResponse])
def list_attributes(
    person_id: str,
    domain: Optional[str] = None,
    engine: PersonModelEngine = Depends(get_engine),
):
    return engine.list_attributes(person_id, domain)


@router.post("/triggers", response_model=PersonTriggerResponse)
def add_trigger(payload: PersonTriggerCreate, engine: PersonModelEngine = Depends(get_engine)):
    try:
        trigger = engine.add_trigger(
            person_id=payload.person_id,
            trigger=payload.trigger,
            response_seen=payload.response_seen,
            occurrences=payload.occurrences,
            calming_measure=payload.calming_measure,
            category=payload.category,
            evidence_ids=payload.evidence_ids,
            last_observed_at=payload.last_observed_at,
            confidence=payload.confidence,
            possible_context=payload.possible_context,
        )
        engine.db.commit()
        return trigger
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@cached_response(ttl=300, key_prefix="person_triggers")
@router.get("/{person_id}/triggers", response_model=List[PersonTriggerResponse])
def list_triggers(person_id: str, engine: PersonModelEngine = Depends(get_engine)):
    return engine.list_triggers(person_id)


@router.post("/calmers", response_model=PersonCalmerResponse)
def add_calmer(payload: PersonCalmerCreate, engine: PersonModelEngine = Depends(get_engine)):
    try:
        calmer = engine.add_calmer(
            person_id=payload.person_id,
            measure=payload.measure,
            context=payload.context,
            evidence_ids=payload.evidence_ids,
            confidence=payload.confidence,
        )
        engine.db.commit()
        return calmer
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@cached_response(ttl=300, key_prefix="person_calmers")
@router.get("/{person_id}/calmers", response_model=List[PersonCalmerResponse])
def list_calmers(person_id: str, engine: PersonModelEngine = Depends(get_engine)):
    return engine.list_calmers(person_id)


# ----------------------------------------------------------------------
# Behaviour
# ----------------------------------------------------------------------

behavior_router = APIRouter(prefix="/behavior", tags=["behavior"])


def get_behavior_engine(db: Session = Depends(get_db)) -> BehaviorEngine:
    return BehaviorEngine(db)


@behavior_router.post("/observe", response_model=dict)
def record_observation(
    payload: BehaviorObservationCreate,
    engine: BehaviorEngine = Depends(get_behavior_engine),
):
    try:
        observation = engine.record_observation(
            person_id=payload.person_id,
            description=payload.description,
            category=payload.category,
            original_text=payload.original_text,
            occurred_at=payload.occurred_at,
            time_of_day=payload.time_of_day,
            location=payload.location,
            observed_by_caregiver_id=payload.observed_by_caregiver_id,
            preceding_context=payload.preceding_context,
            response_taken=payload.response_taken,
            duration_minutes=payload.duration_minutes,
            evidence_ids=payload.evidence_ids,
            is_safety_concern=payload.is_safety_concern,
        )
        engine.db.commit()
        return {"ok": True, "observation_id": observation.id}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@behavior_router.post("/interpret/{observation_id}", response_model=BehaviorInterpretationResponse)
def interpret(
    observation_id: str,
    person_id: Optional[str] = None,
    engine: BehaviorEngine = Depends(get_behavior_engine),
):
    try:
        interpretation = engine.interpret(observation_id, person_id)
        engine.db.commit()
        return interpretation
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@behavior_router.get("/view/{person_id}")
def behaviour_view(person_id: str, engine: BehaviorEngine = Depends(get_behavior_engine)):
    return engine.behaviour_view(person_id)


@behavior_router.post("/hypotheses/{hypothesis_id}/reject")
def reject_hypothesis(
    hypothesis_id: str,
    payload: BehaviorHypothesisReject,
    engine: BehaviorEngine = Depends(get_behavior_engine),
):
    try:
        hypothesis = engine.reject_hypothesis(
            hypothesis_id, payload.reason, payload.caregiver_id
        )
        engine.db.commit()
        return {"ok": True, "hypothesis_id": hypothesis.id, "rejected": not hypothesis.is_active}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
