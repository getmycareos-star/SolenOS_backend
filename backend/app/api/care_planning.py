from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional

from app.core.database import get_db
from app.schemas.care_context import (
    CareDecisionCreate,
    CareDecisionOutcome,
    CareDecisionResponse,
    CarePlanCreate,
    CarePlanResponse,
    ContradictionAnalysisResponse,
    ContradictionClose,
    ContradictionResolutionCreate,
)
from app.services.care_planning import CarePlannerEngine
from app.services.contradiction import ContradictionEngine

router = APIRouter(prefix="/care-planning", tags=["care-planning"])


def get_engine(db: Session = Depends(get_db)) -> CarePlannerEngine:
    return CarePlannerEngine(db)


@router.post("/decision", response_model=CareDecisionResponse)
def propose_decision(
    payload: CareDecisionCreate, engine: CarePlannerEngine = Depends(get_engine)
):
    try:
        decision = engine.propose_decision(
            person_id=payload.person_id,
            question=payload.question,
            context=payload.context,
            urgency=payload.urgency,
            evidence_ids=payload.evidence_ids,
            subject_type=payload.subject_type,
            subject_id=payload.subject_id,
        )
        engine.db.commit()
        return decision
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/{person_id}/decisions", response_model=List[CareDecisionResponse])
def list_decisions(
    person_id: str,
    status: Optional[str] = None,
    engine: CarePlannerEngine = Depends(get_engine),
):
    return engine.list_decisions(person_id, status)


@router.post("/decision/{decision_id}/decide", response_model=CareDecisionResponse)
def decide(
    decision_id: str,
    payload: CareDecisionOutcome,
    engine: CarePlannerEngine = Depends(get_engine),
):
    try:
        decision = engine.decide(
            decision_id, payload.accept, payload.note, payload.caregiver_id
        )
        engine.db.commit()
        return decision
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/plan", response_model=CarePlanResponse)
def build_plan(payload: CarePlanCreate, engine: CarePlannerEngine = Depends(get_engine)):
    try:
        plan = engine.build_plan(
            person_id=payload.person_id,
            decision_id=payload.decision_id,
            title=payload.title,
            evidence_ids=payload.evidence_ids,
        )
        engine.db.commit()
        return plan
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/{person_id}/plans", response_model=List[CarePlanResponse])
def list_plans(person_id: str, engine: CarePlannerEngine = Depends(get_engine)):
    return engine.list_plans(person_id)


@router.get("/plan/{plan_id}/progress")
def plan_progress(plan_id: str, engine: CarePlannerEngine = Depends(get_engine)):
    try:
        return engine.plan_progress(plan_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


# ----------------------------------------------------------------------
# Contradiction
# ----------------------------------------------------------------------

contradiction_router = APIRouter(prefix="/contradiction", tags=["contradiction"])


def get_contradiction_engine(db: Session = Depends(get_db)) -> ContradictionEngine:
    return ContradictionEngine(db)


@contradiction_router.get("/{person_id}/unresolved")
def unresolved(person_id: str, engine: ContradictionEngine = Depends(get_contradiction_engine)):
    return engine.unresolved_summary(person_id)


@contradiction_router.post(
    "/{contradiction_id}/analyse", response_model=ContradictionAnalysisResponse
)
def analyse(
    contradiction_id: str,
    person_id: Optional[str] = None,
    engine: ContradictionEngine = Depends(get_contradiction_engine),
):
    try:
        analysis = engine.analyse(contradiction_id, person_id)
        engine.db.commit()
        return analysis
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@contradiction_router.post("/resolution")
def record_resolution(
    payload: ContradictionResolutionCreate,
    engine: ContradictionEngine = Depends(get_contradiction_engine),
):
    try:
        resolution = engine.record_resolution(
            analysis_id=payload.analysis_id,
            path_taken=payload.path_taken,
            outcome=payload.outcome,
            reason=payload.reason,
            remaining_uncertainty=payload.remaining_uncertainty,
            caregiver_id=payload.caregiver_id,
        )
        engine.db.commit()
        return {"ok": True, "resolution_id": resolution.id}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@contradiction_router.post("/{contradiction_id}/close")
def close(
    contradiction_id: str,
    payload: ContradictionClose,
    engine: ContradictionEngine = Depends(get_contradiction_engine),
):
    try:
        contradiction = engine.close_contradiction(
            contradiction_id, payload.notes, payload.caregiver_id
        )
        engine.db.commit()
        return {
            "ok": True,
            "contradiction_id": contradiction.id,
            "resolution_status": contradiction.resolution_status,
        }
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
