from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime
from app.core.database import get_db
from app.models.care import CareEvent
from app.models.change import Baseline, Change, Pattern, Situation
from app.schemas.change import (
    BaselineCreate,
    Baseline as BaselineSchema,
    Change as ChangeSchema,
    Pattern as PatternSchema,
    Situation as SituationSchema,
    SituationResponse,
)
from app.services.change_detection import detect_event_changes
from app.services.situation_formation import detect_situations, update_situations
from app.services.explanation import explain_situation

router = APIRouter(prefix="/change", tags=["change"])


@router.post("/baselines", response_model=BaselineSchema)
def create_baseline(baseline: BaselineCreate, db: Session = Depends(get_db)):
    db_baseline = Baseline(**baseline.model_dump())
    db.add(db_baseline)
    db.commit()
    db.refresh(db_baseline)
    return db_baseline


@router.get("/baselines/{person_id}", response_model=List[BaselineSchema])
def list_baselines(person_id: str, subject_type: Optional[str] = None, db: Session = Depends(get_db)):
    query = db.query(Baseline).filter(Baseline.person_id == person_id)
    if subject_type:
        query = query.filter(Baseline.subject_type == subject_type)
    return query.order_by(Baseline.created_at.desc()).all()


@router.get("/changes/detect", response_model=List[ChangeSchema])
def detect_changes(
    person_id: str,
    event_type: str,
    subject_id: Optional[str] = None,
    reference_time: Optional[str] = None,
    db: Session = Depends(get_db),
):
    ref_time = datetime.fromisoformat(reference_time.replace("Z", "+00:00")) if reference_time else None
    changes = detect_event_changes(db, person_id, event_type, subject_id, ref_time)
    return changes


@router.get("/changes/{person_id}", response_model=List[ChangeSchema])
def list_changes(person_id: str, subject_type: Optional[str] = None, db: Session = Depends(get_db)):
    query = db.query(Change).filter(Change.person_id == person_id)
    if subject_type:
        query = query.filter(Change.subject_type == subject_type)
    return query.order_by(Change.created_at.desc()).all()


@router.get("/patterns/detect", response_model=List[PatternSchema])
def detect_patterns_endpoint(
    person_id: str,
    event_type: str,
    subject_id: Optional[str] = None,
    reference_time: Optional[str] = None,
    db: Session = Depends(get_db),
):
    from app.services.pattern_detection import detect_patterns as _detect_patterns
    ref_time = datetime.fromisoformat(reference_time.replace("Z", "+00:00")) if reference_time else None
    patterns = _detect_patterns(db, person_id, event_type, subject_id, ref_time)
    return patterns


@router.get("/patterns/{person_id}", response_model=List[PatternSchema])
def list_patterns(person_id: str, subject_type: Optional[str] = None, db: Session = Depends(get_db)):
    query = db.query(Pattern).filter(Pattern.person_id == person_id)
    if subject_type:
        query = query.filter(Pattern.subject_type == subject_type)
    return query.order_by(Pattern.created_at.desc()).all()


@router.get("/situations/detect", response_model=List[SituationResponse])
def detect_situations_endpoint(
    person_id: str,
    reference_time: Optional[str] = None,
    db: Session = Depends(get_db),
):
    ref_time = datetime.fromisoformat(reference_time.replace("Z", "+00:00")) if reference_time else None
    situations = detect_situations(db, person_id, ref_time)
    result = []
    for s in situations:
        changes = db.query(Change).filter(Change.id.in_(s.change_ids or [])).all() if s.change_ids else []
        patterns = db.query(Pattern).filter(Pattern.id.in_(s.pattern_ids or [])).all() if s.pattern_ids else []
        explanation = explain_situation(s, changes, patterns)
        result.append(SituationResponse(
            ok=True,
            situation_id=s.id,
            person_id=s.person_id,
            title=s.title,
            description=s.description,
            situation_type=s.situation_type,
            state=s.state,
            confidence=s.confidence,
            explanation=explanation,
            signals=[
                {
                    "id": sid,
                    "type": stype,
                }
                for sid, stype in zip(s.signal_ids, s.signal_types)
            ],
            patterns=[
                {
                    "id": p.id,
                    "type": p.pattern_type.value,
                    "strength": p.pattern_strength.value,
                }
                for p in patterns
            ],
            changes=[
                {
                    "id": c.id,
                    "type": c.change_type.value,
                    "direction": c.change_direction.value,
                    "significance_verdict": c.significance_verdict.value if c.significance_verdict else None,
                    "confidence": c.confidence,
                    "evidence_ids": c.evidence_ids or [],
                }
                for c in changes
            ],
            alternative_interpretations=s.alternative_interpretations,
            attention_candidate=s.attention_candidate,
            follow_up_candidate=s.follow_up_candidate,
            context=s.context,
            temporal_start=s.temporal_start,
            temporal_end=s.temporal_end,
        ))
    return result


@router.get("/situations/{person_id}", response_model=List[SituationSchema])
def list_situations(person_id: str, db: Session = Depends(get_db)):
    return db.query(Situation).filter(Situation.person_id == person_id).order_by(Situation.created_at.desc()).all()


@router.post("/situations/update", response_model=List[SituationSchema])
def update_situations_endpoint(
    person_id: str,
    event_id: str,
    reference_time: Optional[str] = None,
    db: Session = Depends(get_db),
):
    event = db.query(CareEvent).filter(CareEvent.id == event_id, CareEvent.person_id == person_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    ref_time = datetime.fromisoformat(reference_time.replace("Z", "+00:00")) if reference_time else None
    updated = update_situations(db, person_id, event, ref_time)
    return updated
