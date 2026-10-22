from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime, timezone

from app.core.database import get_db
from app.models.care_continuity import CareActionOutcome, CareAssertion
from app.models.uncertainty import Contradiction
from app.schemas.care_continuity import (
    CareActionOutcomeCreate, CareActionOutcomeResponse,
    ContradictionResolutionRequest,
    CareAssertionCreate, CareAssertionResponse, CareAssertionStatusUpdate,
    CareStateRevisionResponse, FreshnessResponse, NextQuestionResponse,
)
from app.services.care_continuity import (
    create_action_outcome, create_assertion, get_freshness, list_revisions,
    next_best_questions, revision_response, set_assertion_status,
)

router = APIRouter(prefix="/care-continuity", tags=["care-continuity"])


@router.get("/revisions/{person_id}", response_model=List[CareStateRevisionResponse])
def get_care_state_revisions(
    person_id: str,
    target_type: Optional[str] = None,
    target_id: Optional[str] = None,
    db: Session = Depends(get_db),
):
    return [revision_response(item) for item in list_revisions(db, person_id, target_type, target_id)]


@router.post("/assertions", response_model=CareAssertionResponse, status_code=201)
def submit_care_assertion(payload: CareAssertionCreate, db: Session = Depends(get_db)):
    return create_assertion(db, payload)


@router.get("/assertions/{person_id}", response_model=List[CareAssertionResponse])
def list_care_assertions(person_id: str, db: Session = Depends(get_db)):
    return db.query(CareAssertion).filter(
        CareAssertion.person_id == person_id
    ).order_by(CareAssertion.recorded_at.desc()).limit(500).all()


@router.patch("/assertions/{assertion_id}/status", response_model=CareAssertionResponse)
def update_care_assertion_status(
    assertion_id: str, payload: CareAssertionStatusUpdate, db: Session = Depends(get_db)
):
    return set_assertion_status(db, assertion_id, payload.person_id, payload.assertion_status)


@router.get("/freshness/{person_id}", response_model=List[FreshnessResponse])
def get_assertion_freshness(person_id: str, db: Session = Depends(get_db)):
    assertions = db.query(CareAssertion).filter(
        CareAssertion.person_id == person_id
    ).order_by(CareAssertion.recorded_at.desc()).limit(500).all()
    return [get_freshness(item) for item in assertions]


@router.post("/next-questions/{person_id}", response_model=List[NextQuestionResponse])
def generate_next_best_questions(person_id: str, db: Session = Depends(get_db)):
    return next_best_questions(db, person_id, persist=True)


@router.post("/outcomes", response_model=CareActionOutcomeResponse, status_code=201)
def record_care_action_outcome(payload: CareActionOutcomeCreate, db: Session = Depends(get_db)):
    return create_action_outcome(db, payload)


@router.get("/outcomes/{person_id}", response_model=List[CareActionOutcomeResponse])
def list_care_action_outcomes(person_id: str, db: Session = Depends(get_db)):
    return db.query(CareActionOutcome).filter(
        CareActionOutcome.person_id == person_id
    ).order_by(CareActionOutcome.created_at.desc()).limit(500).all()



@router.get("/contradictions/{person_id}")
def list_care_continuity_contradictions(person_id: str, db: Session = Depends(get_db)):
    return db.query(Contradiction).filter(
        Contradiction.person_id == person_id
    ).order_by(Contradiction.created_at.desc()).limit(500).all()


@router.patch("/contradictions/{contradiction_id}/resolution")
def resolve_care_continuity_contradiction(
    contradiction_id: str,
    payload: ContradictionResolutionRequest,
    db: Session = Depends(get_db),
):
    contradiction = db.query(Contradiction).filter(
        Contradiction.id == contradiction_id,
        Contradiction.person_id == payload.person_id,
    ).first()
    if contradiction is None:
        raise HTTPException(status_code=404, detail="Contradiction not found for this care record")
    contradiction.resolution_status = payload.resolution_status
    contradiction.resolution_notes = payload.resolution_notes.strip()
    contradiction.resolved_at = datetime.now(timezone.utc)
    contradiction.resolved_by_caregiver_id = payload.resolved_by_caregiver_id
    db.commit()
    db.refresh(contradiction)
    return contradiction
