from fastapi import APIRouter, Depends, HTTPException, Request, Query
import hashlib
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime, timezone
from app.core.database import get_db
from app.models.care import CareEvent
from app.models.care_continuity import CareAssertion
from app.models.anonymous_session import AnonymousSession
from app.models.change import Baseline, Change, ChangeConfirmationEvent, Pattern, Situation
from app.schemas.change import (
    BaselineCreate,
    Baseline as BaselineSchema,
    Change as ChangeSchema,
    Pattern as PatternSchema,
    Situation as SituationSchema,
    SituationResponse,
    ChangeDetailResponse,
    ChangeConfirmationRequest,
    ChangeConfirmationTransitionResponse,
)
from app.services.change_detection import detect_event_changes, detect_assertion_changes
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
        )
        .first()
    )
    if session is None:
        raise HTTPException(status_code=401, detail="The SolenOS care session is invalid or expired")
    # SQLite returns timezone-naive datetimes for DateTime(timezone=True). Normalize
    # the loaded value before expiry comparison so the API and tests use the same
    # absolute-time semantics as PostgreSQL rather than relying on dialect-specific
    # SQL string comparison for timestamps.
    expires_at = session.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= datetime.now(timezone.utc):
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
        from app.services.pattern_detection import detect_patterns as reconcile_patterns

        for event_type in event_types:
            detect_event_changes(db, person_id, event_type, None)
            # Pattern reconciliation runs only after the evidence-backed change
            # assessments have been flushed into this transaction.
            reconcile_patterns(db, person_id, event_type, None)
        detect_assertion_changes(db, person_id)
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


@router.post(
    "/changes/{person_id}/{change_id}/confirmation",
    response_model=ChangeConfirmationTransitionResponse,
)
def transition_change_confirmation(
    person_id: str,
    change_id: str,
    payload: ChangeConfirmationRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """Record an audited caregiver confirmation decision without rewriting source evidence."""
    session = _require_person_scope(request, db, person_id)
    allowed_statuses = {"awaiting_confirmation", "confirmed", "rejected", "inconclusive"}
    if payload.status not in allowed_statuses:
        raise HTTPException(status_code=422, detail="Unsupported confirmation status")
    if payload.status in {"confirmed", "rejected", "inconclusive"} and not (payload.reason or "").strip():
        raise HTTPException(
            status_code=422,
            detail="A decision rationale is required for confirmed, rejected, or inconclusive decisions",
        )

    change = (
        db.query(Change)
        .filter(Change.id == change_id, Change.person_id == person_id)
        .with_for_update()
        .first()
    )
    if change is None:
        raise HTTPException(status_code=404, detail="Change assessment not found")
    if not change.is_active or change.superseded_by_change_id:
        raise HTTPException(status_code=409, detail="A superseded change cannot be confirmed or rejected")
    previous_status = change.confirmation_status or "detected"
    transitions = {
        "detected": {"awaiting_confirmation", "confirmed", "rejected", "inconclusive"},
        "awaiting_confirmation": {"confirmed", "rejected", "inconclusive"},
        "confirmed": {"awaiting_confirmation"},
        "rejected": {"awaiting_confirmation"},
        "inconclusive": {"awaiting_confirmation"},
    }
    if payload.status not in transitions.get(previous_status, set()):
        raise HTTPException(
            status_code=409,
            detail=f"Invalid confirmation transition from {previous_status} to {payload.status}",
        )

    if payload.status == "confirmed" and not payload.evidence_ids:
        raise HTTPException(
            status_code=422,
            detail="At least one persisted source event or assertion is required to confirm a change",
        )

    supplied_evidence_ids = list(dict.fromkeys(payload.evidence_ids))
    if supplied_evidence_ids:
        valid_event_ids = {
            row[0]
            for row in db.query(CareEvent.id).filter(
                CareEvent.person_id == person_id,
                CareEvent.id.in_(supplied_evidence_ids),
            ).all()
        }
        valid_assertion_ids = {
            row[0]
            for row in db.query(CareAssertion.id).filter(
                CareAssertion.person_id == person_id,
                CareAssertion.id.in_(supplied_evidence_ids),
            ).all()
        }
        valid_ids = valid_event_ids | valid_assertion_ids
        if valid_ids != set(supplied_evidence_ids):
            raise HTTPException(
                status_code=422,
                detail="Every confirmation evidence reference must identify a persisted event or assertion in this care record",
            )

    transition = ChangeConfirmationEvent(
        change_id=change.id,
        person_id=person_id,
        actor_caregiver_id=session.caregiver_id,
        previous_status=previous_status,
        new_status=payload.status,
        reason=(payload.reason or "").strip() or None,
        evidence_ids=supplied_evidence_ids,
    )
    change.confirmation_status = payload.status
    db.add(transition)
    try:
        db.commit()
        db.refresh(change)
        db.refresh(transition)
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=503, detail="Confirmation transition could not be persisted") from exc
    return ChangeConfirmationTransitionResponse(
        change=change,
        transition_id=transition.id,
        previous_status=previous_status,
        new_status=payload.status,
        actor_caregiver_id=session.caregiver_id,
        created_at=transition.created_at,
    )


@router.get("/changes/{person_id}/{change_id}", response_model=ChangeDetailResponse)
def get_change_detail(
    person_id: str,
    change_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Return one persisted assessment with only its source evidence for this person."""
    _require_person_scope(request, db, person_id)
    change = (
        db.query(Change)
        .filter(Change.id == change_id, Change.person_id == person_id)
        .first()
    )
    if change is None:
        raise HTTPException(status_code=404, detail="Change assessment not found")

    evidence_ids = list(dict.fromkeys(change.evidence_ids or []))
    evidence = []
    if evidence_ids:
        evidence = (
            db.query(CareEvent)
            .filter(
                CareEvent.person_id == person_id,
                CareEvent.id.in_(evidence_ids),
            )
            .order_by(CareEvent.occurred_at.asc(), CareEvent.id.asc())
            .all()
        )
    assertion_evidence = []
    if evidence_ids:
        assertion_evidence = (
            db.query(CareAssertion)
            .filter(
                CareAssertion.person_id == person_id,
                CareAssertion.id.in_(evidence_ids),
            )
            .order_by(CareAssertion.observed_at.asc(), CareAssertion.recorded_at.asc(), CareAssertion.id.asc())
            .all()
        )
    # Do not return arbitrary source records referenced by stale or corrupt IDs.
    history = (
        db.query(ChangeConfirmationEvent)
        .filter(
            ChangeConfirmationEvent.change_id == change.id,
            ChangeConfirmationEvent.person_id == person_id,
        )
        .order_by(ChangeConfirmationEvent.created_at.asc(), ChangeConfirmationEvent.id.asc())
        .all()
    )
    return ChangeDetailResponse(
        change=change,
        evidence=evidence,
        assertions=assertion_evidence,
        confirmation_history=history,
    )


@router.get("/changes/{person_id}", response_model=List[ChangeSchema])
def list_changes(
    person_id: str,
    request: Request,
    subject_type: Optional[str] = None,
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    _require_person_scope(request, db, person_id)
    query = db.query(Change).filter(Change.person_id == person_id)
    if subject_type:
        query = query.filter(Change.subject_type == subject_type)
    return query.order_by(Change.created_at.desc(), Change.id.asc()).offset(offset).limit(limit).all()


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
def list_patterns(
    person_id: str,
    request: Request,
    subject_type: Optional[str] = None,
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    _require_person_scope(request, db, person_id)
    query = db.query(Pattern).filter(Pattern.person_id == person_id)
    if subject_type:
        query = query.filter(Pattern.subject_type == subject_type)
    return query.order_by(Pattern.created_at.desc(), Pattern.id.asc()).offset(offset).limit(limit).all()


@router.get("/patterns/{person_id}/{pattern_id}")
def get_pattern_detail(
    person_id: str,
    pattern_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Return one persisted pattern with only same-person source events and changes."""
    _require_person_scope(request, db, person_id)
    pattern = (
        db.query(Pattern)
        .filter(Pattern.id == pattern_id, Pattern.person_id == person_id)
        .first()
    )
    if pattern is None:
        raise HTTPException(status_code=404, detail="Pattern not found")
    event_ids = list(dict.fromkeys(pattern.event_ids or []))
    change_ids = list(dict.fromkeys(pattern.change_ids or []))
    events = (
        db.query(CareEvent)
        .filter(CareEvent.person_id == person_id, CareEvent.id.in_(event_ids))
        .order_by(CareEvent.occurred_at.asc(), CareEvent.id.asc())
        .all()
        if event_ids else []
    )
    changes = (
        db.query(Change)
        .filter(Change.person_id == person_id, Change.id.in_(change_ids))
        .order_by(Change.created_at.asc(), Change.id.asc())
        .all()
        if change_ids else []
    )
    return {
        "pattern": pattern,
        "events": [
            {
                "id": event.id,
                "event_type": event.event_type,
                "status": event.status,
                "occurred_at": event.occurred_at,
                "recorded_at": event.recorded_at,
                "title": event.title,
                "description": event.description,
                "evidence_ids": event.evidence_ids or [],
                "time_provenance": event.time_provenance,
            }
            for event in events
        ],
        "changes": [
            {
                "id": change.id,
                "change_type": change.change_type.value,
                "change_direction": change.change_direction.value,
                "previous_state": change.previous_state,
                "current_state": change.current_state,
                "explanation": change.explanation,
                "evidence_ids": change.evidence_ids or [],
                "confirmation_status": change.confirmation_status,
            }
            for change in changes
        ],
    }


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


# Coverage is recorded independently of event occurrence. It describes observation,
# not whether an event did or did not happen.
from app.models.care import Evidence
from app.models.observation_coverage import ObservationCoverage
from app.schemas.observation_coverage import (
    ObservationCoverageCreate, ObservationCoverageRecord, CoverageAssessment,
)
from app.services.observation_coverage import assess_observation_coverage


@router.post("/coverage", response_model=ObservationCoverageRecord, status_code=201)
def record_observation_coverage(
    payload: ObservationCoverageCreate, request: Request, db: Session = Depends(get_db),
):
    session = _require_person_scope(request, db, payload.person_id)
    if payload.window_end > datetime.now(timezone.utc):
        raise HTTPException(status_code=422, detail="Coverage windows cannot end in the future")
    source_ids = list(dict.fromkeys(payload.source_evidence_ids))
    if source_ids:
        valid_ids = {
            row[0] for row in db.query(Evidence.id).filter(
                Evidence.person_id == payload.person_id, Evidence.id.in_(source_ids)
            ).all()
        }
        if valid_ids != set(source_ids):
            raise HTTPException(status_code=422, detail="Coverage evidence must belong to this care record")
    row = ObservationCoverage(
        person_id=payload.person_id,
        subject_type=payload.subject_type.strip(),
        window_start=payload.window_start,
        window_end=payload.window_end,
        coverage_status=payload.coverage_status,
        source_evidence_ids=source_ids,
        rationale=payload.rationale.strip(),
        recorded_by_caregiver_id=session.caregiver_id,
    )
    db.add(row)
    try:
        db.commit()
        db.refresh(row)
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=503, detail="Observation coverage could not be persisted") from exc
    return row


@router.get("/coverage/{person_id}", response_model=CoverageAssessment)
def get_observation_coverage(
    person_id: str,
    request: Request,
    subject_type: str = Query(..., min_length=1, max_length=120),
    window_start: datetime = Query(...),
    window_end: datetime = Query(...),
    db: Session = Depends(get_db),
):
    _require_person_scope(request, db, person_id)
    if (
        window_start.tzinfo is None or window_start.utcoffset() is None
        or window_end.tzinfo is None or window_end.utcoffset() is None
    ):
        raise HTTPException(
            status_code=422,
            detail="window_start and window_end must include explicit timezone offsets",
        )
    if window_end <= window_start:
        raise HTTPException(status_code=422, detail="window_end must be later than window_start")
    return assess_observation_coverage(db, person_id, subject_type.strip(), window_start, window_end)
