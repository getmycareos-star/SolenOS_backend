from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional

from app.core.care_context_enums import MattersCategory, MattersImportance
from app.core.database import get_db
from app.models.what_matters import AlignmentAssessment
from app.schemas.care_context import (
    AlignmentRequest,
    TrajectoryPointCreate,
    TrajectoryResponse,
    TrajectoryTrackRequest,
    WhatMattersCreate,
    WhatMattersResponse,
)
from app.services.trajectory import TrajectoryEngine
from app.services.what_matters import WhatMattersEngine

router = APIRouter(prefix="/what-matters", tags=["what-matters"])


def get_engine(db: Session = Depends(get_db)) -> WhatMattersEngine:
    return WhatMattersEngine(db)


@router.post("/entry", response_model=WhatMattersResponse)
def add_entry(payload: WhatMattersCreate, engine: WhatMattersEngine = Depends(get_engine)):
    try:
        entry = engine.add_entry(
            person_id=payload.person_id,
            statement=payload.statement,
            category=payload.category,
            importance=payload.importance,
            who_it_matters_to=payload.who_it_matters_to,
            how_we_know=payload.how_we_know,
            what_it_rules_out=payload.what_it_rules_out,
            source_quote=payload.source_quote,
            stated_by_caregiver_id=payload.stated_by_caregiver_id,
            evidence_ids=payload.evidence_ids,
            confidence=payload.confidence,
            possible_context=payload.possible_context,
        )
        engine.db.commit()
        return entry
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/{person_id}/entries", response_model=List[WhatMattersResponse])
def list_entries(
    person_id: str,
    category: Optional[MattersCategory] = None,
    minimum_importance: Optional[MattersImportance] = None,
    engine: WhatMattersEngine = Depends(get_engine),
):
    return engine.list_entries(person_id, category, minimum_importance)


@router.get("/{person_id}/view")
def what_matters_view(person_id: str, engine: WhatMattersEngine = Depends(get_engine)):
    return engine.what_matters_view(person_id)


@router.post("/extract")
def extract(person_id: str, engine: WhatMattersEngine = Depends(get_engine)):
    created = engine.extract(person_id)
    engine.db.commit()
    return {
        "ok": True,
        "created": len(created),
        "note": (
            "Proposals only. Each entry was checked by the trust guard and rejected if the record "
            "does not support it."
        ),
    }


@router.post("/alignment")
def assess_action(payload: AlignmentRequest, engine: WhatMattersEngine = Depends(get_engine)):
    try:
        assessment = engine.assess_action(
            person_id=payload.person_id,
            action_description=payload.action_description,
            action_ref_type=payload.action_ref_type,
            action_ref_id=payload.action_ref_id,
        )
        engine.db.commit()
        return assessment
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/{person_id}/alignment")
def list_alignments(
    person_id: str, engine: WhatMattersEngine = Depends(get_engine)
):
    return (
        engine.db.query(AlignmentAssessment)
        .filter(AlignmentAssessment.person_id == person_id)
        .order_by(AlignmentAssessment.created_at.desc())
        .limit(50)
        .all()
    )


# ----------------------------------------------------------------------
# Trajectory
# ----------------------------------------------------------------------

trajectory_router = APIRouter(prefix="/trajectory", tags=["trajectory"])


def get_trajectory_engine(db: Session = Depends(get_db)) -> TrajectoryEngine:
    return TrajectoryEngine(db)


@trajectory_router.post("/point")
def record_point(
    payload: TrajectoryPointCreate,
    engine: TrajectoryEngine = Depends(get_trajectory_engine),
):
    try:
        point = engine.record_point(
            person_id=payload.person_id,
            subject=payload.subject,
            observed_at=payload.observed_at,
            value=payload.value,
            value_text=payload.value_text,
            evidence_ids=payload.evidence_ids,
            source_text=payload.source_text,
            observed_by_caregiver_id=payload.observed_by_caregiver_id,
            is_estimate=payload.is_estimate,
        )
        engine.db.commit()
        return {"ok": True, "point_id": point.id}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@trajectory_router.post("/track", response_model=TrajectoryResponse)
def track(
    payload: TrajectoryTrackRequest,
    engine: TrajectoryEngine = Depends(get_trajectory_engine),
):
    try:
        trajectory = engine.track(
            person_id=payload.person_id,
            subject=payload.subject,
            label=payload.label,
            unit=payload.unit,
            baseline_variation=payload.baseline_variation,
        )
        engine.db.commit()
        return trajectory
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@trajectory_router.get("/{person_id}/view")
def trajectory_view(
    person_id: str, engine: TrajectoryEngine = Depends(get_trajectory_engine)
):
    return engine.trajectory_view(person_id)


@trajectory_router.get("/{person_id}/{subject}")
def get_one(
    person_id: str, subject: str, engine: TrajectoryEngine = Depends(get_trajectory_engine)
):
    trajectory = engine.get_trajectory(person_id, subject)
    if trajectory is None:
        raise HTTPException(status_code=404, detail="Trajectory not found")
    return trajectory
