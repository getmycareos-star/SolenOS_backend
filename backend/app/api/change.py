from fastapi import APIRouter, Depends, HTTPException, Request
import hashlib
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime, timezone
from app.core.database import get_db
from app.models.care import CareEvent
from app.models.anonymous_session import AnonymousSession
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


def _require_person_scope(request: Request, db: Session, person_id: str) -> AnonymousSession:
    """Require a live server-issued bearer session scoped to the requested care record."""
    authorization = request.headers.get("authorization", "")
    scheme, _, raw_token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not raw_token.strip():
        raise HTTPException(status_code=401, detail="A valid SolenOS care session is required")
    token_hash = hashlib.sha256(raw_token.strip().encode("utf-8")).hexdigest()
    session = (
        db.query(AnonymousSession)
        .filter(
            AnonymousSession.token_hash == token_hash,
            AnonymousSession.revoked_at.is_(None),
            AnonymousSession.expires_at > datetime.now(timezone.utc),
        )
        .first()
    )
    if session is None:
        raise HTTPException(status_code=401, detail="The SolenOS care session is invalid or expired")
    if session.person_id != person_id:
        raise HTTPException(status_code=403, detail="This session cannot access the requested care record")
    return session


def _parse_reference_time(value: Optional[str]) -> Optional[datetime]:
    """Parse optional ISO-8601 reference time and return a client error if invalid.

    Naive timestamps are interpreted as UTC for backwards compatibility.
    """
    if value is None or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=422,
            detail="reference_time must be a valid ISO-8601 timestamp",
        ) from exc
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


@router.post("/baselines", response_model=BaselineSchema)
def create_baseline(baseline: BaselineCreate, request: Request, db: Session = Depends(get_db)):
    _require_person_scope(request, db, baseline.person_id)
    raise HTTPException(
        status_code=405,
        detail="Baselines are derived from persisted care events and cannot be supplied by clients",
    )


@router.get("/baselines/{person_id}", response_model=List[BaselineSchema])
def list_baselines(person_id: str, request: Request, subject_type: Optional[str] = None, db: Session = Depends(get_db)):
    _require_person_scope(request, db, person_id)
    query = db.query(Baseline).filter(Baseline.person_id == person_id)
    if subject_type:
        query = query.filter(Baseline.subject_type == subject_type)
    return query.order_by(Baseline.created_at.desc()).all()


@router.get("/changes/detect", response_model=List[ChangeSchema])
def detect_changes(
    person_id: str,
    event_type: str,
    request: Request,
    subject_id: Optional[str] = None,
    reference_time: Optional[str] = None,
    db: Session = Depends(get_db),
):
    _require_person_scope(request, db, person_id)
    ref_time = _parse_reference_time(reference_time)
    changes = detect_event_changes(db, person_id, event_type, subject_id, ref_time)
    db.commit()
    return changes


@router.post("/changes/reassess", response_model=List[ChangeSchema])
def reassess_changes(person_id: str, request: Request, db: Session = Depends(get_db)):
    """Reconcile change assessments from this person's persisted care events only."""
    _require_person_scope(request, db, person_id)
    event_types = [
        row[0]
        for row in db.query(CareEvent.event_type)
        .filter(
            CareEvent.person_id == person_id,
            CareEvent.event_type.is_not(None),
        )
        .distinct()
        .order_by(CareEvent.event_type.asc())
        .all()
        if row[0]
    ]
    try:
        for event_type in event_types:
            detect_event_changes(db, person_id, event_type, None)
        db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(
            status_code=503,
            detail="Persisted change reassessment could not be completed",
        ) from exc
    return (
        db.query(Change)
        .filter(Change.person_id == person_id, Change.is_active.is_(True))
        .order_by(Change.created_at.desc())
        .limit(100)
        .all()
    )


@router.get("/changes/{person_id}", response_model=List[ChangeSchema])
def list_changes(person_id: str, request: Request, subject_type: Optional[str] = None, db: Session = Depends(get_db)):
    _require_person_scope(request, db, person_id)
    query = db.query(Change).filter(Change.person_id == person_id)
    if subject_type:
        query = query.filter(Change.subject_type == subject_type)
    return query.order_by(Change.created_at.desc()).limit(100).all()


@router.get("/patterns/detect", response_model=List[PatternSchema])
def detect_patterns_endpoint(
    person_id: str,
    event_type: str,
    request: Request,
    subject_id: Optional[str] = None,
    reference_time: Optional[str] = None,
    db: Session = Depends(get_db),
):
    _require_person_scope(request, db, person_id)
    from app.services.pattern_detection import detect_patterns as _detect_patterns
    ref_time = _parse_reference_time(reference_time)
    patterns = _detect_patterns(db, person_id, event_type, subject_id, ref_time)
    db.commit()
    return patterns


@router.get("/patterns/{person_id}", response_model=List[PatternSchema])
def list_patterns(person_id: str, request: Request, subject_type: Optional[str] = None, db: Session = Depends(get_db)):
    _require_person_scope(request, db, person_id)
    query = db.query(Pattern).filter(Pattern.person_id == person_id)
    if subject_type:
        query = query.filter(Pattern.subject_type == subject_type)
    return query.order_by(Pattern.created_at.desc()).all()


@router.get("/situations/detect", response_model=List[SituationResponse])
def detect_situations_endpoint(
    person_id: str,
    request: Request,
    reference_time: Optional[str] = None,
    db: Session = Depends(get_db),
):
    _require_person_scope(request, db, person_id)
    ref_time = _parse_reference_time(reference_time)
    situations = detect_situations(db, person_id, ref_time)
    db.commit()
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
def list_situations(person_id: str, request: Request, db: Session = Depends(get_db)):
    _require_person_scope(request, db, person_id)
    return db.query(Situation).filter(Situation.person_id == person_id).order_by(Situation.created_at.desc()).all()


@router.post("/situations/update", response_model=List[SituationSchema])
def update_situations_endpoint(
    person_id: str,
    event_id: str,
    request: Request,
    reference_time: Optional[str] = None,
    db: Session = Depends(get_db),
):
    _require_person_scope(request, db, person_id)
    event = db.query(CareEvent).filter(CareEvent.id == event_id, CareEvent.person_id == person_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    ref_time = _parse_reference_time(reference_time)
    updated = update_situations(db, person_id, event, ref_time)
    db.commit()
    return updated
