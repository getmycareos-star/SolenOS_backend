from fastapi import APIRouter, Depends, HTTPException, Request, Query
import hashlib
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime, timezone
from app.core.database import get_db
from app.models.care import CareEvent
from app.models.care_continuity import CareAssertion, EvidenceObservation
from app.models.anonymous_session import AnonymousSession
from app.models.change import Baseline, Change, ChangeConfirmationEvent, Pattern, Situation
from app.core.change_enums import ChangeType, SituationState
from app.schemas.change import (
    BaselineCreate,
    Baseline as BaselineSchema,
    Change as ChangeSchema,
    Pattern as PatternSchema,
    Situation as SituationSchema,
    SituationResponse,
    SituationStateTransitionRequest,
    SituationStateTransitionResponse,
    SituationDetailResponse,
    SituationSourceRecord,
    ChangeDetailResponse,
    ChangeConfirmationRequest,
    ChangeConfirmationTransitionResponse,
)
from app.services.change_detection import detect_event_changes, detect_assertion_changes, detect_numeric_observation_changes
from app.services.situation_formation import detect_situations, update_situations
from app.services.explanation import explain_situation
from app.models.care import Evidence
from app.models.observation_coverage import ObservationCoverage
from app.schemas.observation_coverage import (
    ObservationCoverageCreate, ObservationCoverageRecord, CoverageAssessment,
)
from app.services.observation_coverage import assess_observation_coverage

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
    try:
        from app.services.change_intelligence_reconciliation import reconcile_person_change_intelligence

        # Shared by this explicit endpoint and source-write paths so the same
        # reconciliation semantics apply to new/late evidence and corrections.
        reconcile_person_change_intelligence(db, person_id)
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
    allowed_statuses = {"awaiting_confirmation", "confirmed", "rejected", "inconclusive", "resolved"}
    if payload.status not in allowed_statuses:
        raise HTTPException(status_code=422, detail="Unsupported confirmation status")
    if payload.status in {"confirmed", "rejected", "inconclusive", "resolved"} and not (payload.reason or "").strip():
        raise HTTPException(
            status_code=422,
            detail="A decision rationale is required for confirmed, rejected, inconclusive, or resolved decisions",
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
    if payload.status == "resolved":
        if change.change_type != ChangeType.CESSATION:
            raise HTTPException(
                status_code=422,
                detail="Only a fully covered cessation candidate can be marked resolved",
            )
        if (
            change.baseline_coverage_status != "covered"
            or change.current_coverage_status != "covered"
            or not change.evidence_ids
        ):
            raise HTTPException(
                status_code=422,
                detail="A resolution decision requires complete observation coverage and linked historical source records",
            )
        if not payload.evidence_ids:
            raise HTTPException(
                status_code=422,
                detail="A resolution decision must reference the saved source records being reviewed",
            )
    previous_status = change.confirmation_status or "detected"
    transitions = {
        "detected": {"awaiting_confirmation", "confirmed", "rejected", "inconclusive", "resolved"},
        "awaiting_confirmation": {"confirmed", "rejected", "inconclusive", "resolved"},
        "confirmed": {"awaiting_confirmation", "resolved"},
        "resolved": {"awaiting_confirmation"},
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
    if payload.status == "resolved" and not set(supplied_evidence_ids).issubset(
        set(change.evidence_ids or [])
    ):
        raise HTTPException(
            status_code=422,
            detail="Resolution evidence must come from the source records linked to this change assessment",
        )
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
        valid_observation_ids = {
            row[0]
            for row in db.query(EvidenceObservation.id).filter(
                EvidenceObservation.person_id == person_id,
                EvidenceObservation.id.in_(supplied_evidence_ids),
            ).all()
        }
        valid_ids = valid_event_ids | valid_assertion_ids | valid_observation_ids
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
    observation_evidence = (
        db.query(EvidenceObservation)
        .filter(
            EvidenceObservation.person_id == person_id,
            EvidenceObservation.id.in_(evidence_ids),
        )
        .order_by(EvidenceObservation.observed_at.asc(), EvidenceObservation.id.asc())
        .all()
    ) if evidence_ids else []
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
        observations=observation_evidence,
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


@router.get(
    "/situations/{person_id}/{situation_id}/detail",
    response_model=SituationDetailResponse,
)
def get_situation_detail(
    person_id: str,
    situation_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    _require_person_scope(request, db, person_id)
    situation = db.query(Situation).filter(
        Situation.id == situation_id,
        Situation.person_id == person_id,
    ).first()
    if situation is None:
        raise HTTPException(status_code=404, detail="Care situation not found")

    source_ids = set(situation.signal_ids or [])
    linked_change_ids = set(situation.change_ids or [])
    linked_pattern_ids = set(situation.pattern_ids or [])
    linked_changes = []
    if linked_change_ids:
        linked_changes = db.query(Change).filter(
            Change.person_id == person_id,
            Change.id.in_(linked_change_ids),
        ).all()
    linked_patterns = []
    if linked_pattern_ids:
        linked_patterns = db.query(Pattern).filter(
            Pattern.person_id == person_id,
            Pattern.id.in_(linked_pattern_ids),
        ).all()
    for change in linked_changes:
        source_ids.update(change.evidence_ids or [])
    for pattern in linked_patterns:
        source_ids.update(pattern.event_ids or [])
        linked_change_ids.update(pattern.change_ids or [])
    if linked_change_ids:
        for change in db.query(Change).filter(
            Change.person_id == person_id,
            Change.id.in_(linked_change_ids),
        ).all():
            source_ids.update(change.evidence_ids or [])

    source_records = []
    events = db.query(CareEvent).filter(
        CareEvent.person_id == person_id,
        CareEvent.id.in_(source_ids or {"__none__"}),
    ).all()
    for event in events:
        source_records.append(SituationSourceRecord(
            id=event.id,
            source_type="care_record",
            title=str(event.title or event.event_type or "Care record")[:240],
            excerpt=str(event.description or "")[:1000] or None,
            occurred_at=event.occurred_at,
            recorded_at=getattr(event, "recorded_at", None) or getattr(event, "created_at", None),
            source_label="Care record",
            status=str(event.status or ""),
            evidence_ids=list(event.evidence_ids or []),
        ))
        source_ids.update(event.evidence_ids or [])

    assertions = db.query(CareAssertion).filter(
        CareAssertion.person_id == person_id,
        CareAssertion.id.in_(source_ids or {"__none__"}),
    ).all()
    for assertion in assertions:
        source_records.append(SituationSourceRecord(
            id=assertion.id,
            source_type="care_statement",
            title=f"{assertion.field}: {assertion.asserted_value}"[:240],
            excerpt=str(assertion.source_text or "")[:1000] or None,
            occurred_at=assertion.observed_at,
            recorded_at=assertion.recorded_at,
            source_label=str(assertion.source_label or "Care statement"),
            status=str(assertion.assertion_status or ""),
            evidence_ids=list(assertion.evidence_ids or []),
            epistemic_status=str(assertion.epistemic_state or ""),
        ))
        source_ids.update(assertion.evidence_ids or [])
        if assertion.source_event_id:
            source_ids.add(str(assertion.source_event_id))

    observations = db.query(EvidenceObservation).filter(
        EvidenceObservation.person_id == person_id,
        EvidenceObservation.id.in_(source_ids or {"__none__"}),
    ).all()
    for observation in observations:
        source_records.append(SituationSourceRecord(
            id=observation.id,
            source_type="observation",
            title=f"{observation.attribute}: {observation.value_text or observation.normalized_value or 'Recorded observation'}"[:240],
            excerpt=str(observation.source_passage or "")[:1000] or None,
            occurred_at=observation.observed_at or observation.occurred_at,
            recorded_at=observation.recorded_at,
            source_label="Saved observation",
            status=str(observation.epistemic_status or ""),
            evidence_ids=[str(observation.evidence_id)] if observation.evidence_id else [],
            epistemic_status=str(observation.epistemic_status or ""),
            negated=observation.negated,
            hedge_marker=observation.hedge_marker,
            ambiguity_note=observation.ambiguity_note,
        ))
        if observation.evidence_id:
            source_ids.add(str(observation.evidence_id))

    evidence_rows = db.query(Evidence).filter(
        Evidence.person_id == person_id,
        Evidence.id.in_(source_ids or {"__none__"}),
    ).all()
    for evidence in evidence_rows:
        source_records.append(SituationSourceRecord(
            id=evidence.id,
            source_type="document",
            title=str(evidence.file_name or evidence.source_type or "Care document")[:240],
            excerpt=str(evidence.source_text or "")[:1000] or None,
            occurred_at=evidence.occurred_at,
            recorded_at=evidence.reported_at or evidence.created_at,
            source_label=str(evidence.reported_by_label or evidence.source_type or "Care document"),
            status=str(evidence.evidence_status or ""),
            evidence_ids=[evidence.id],
        ))

    source_records.sort(
        key=lambda item: (item.occurred_at or item.recorded_at or datetime.min.replace(tzinfo=timezone.utc)).isoformat()
    )
    context = situation.context if isinstance(situation.context, dict) else {}
    state_history = context.get("state_history") if isinstance(context.get("state_history"), list) else []
    return SituationDetailResponse(
        situation=situation,
        source_records=source_records,
        state_history=state_history,
    )


@router.post(
    "/situations/{person_id}/{situation_id}/state",
    response_model=SituationStateTransitionResponse,
)
def transition_situation_state(
    person_id: str,
    situation_id: str,
    payload: SituationStateTransitionRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    session = _require_person_scope(request, db, person_id)
    if not session.caregiver_id:
        raise HTTPException(status_code=403, detail="A caregiver session is required to update a situation")

    situation = db.query(Situation).filter(
        Situation.id == situation_id,
        Situation.person_id == person_id,
    ).with_for_update().first()
    if situation is None:
        raise HTTPException(status_code=404, detail="Care situation not found")
    if not situation.is_active or situation.superseded_by_situation_id:
        raise HTTPException(status_code=409, detail="A past care situation cannot be updated")

    if payload.state == SituationState.EVOLVED:
        raise HTTPException(status_code=422, detail="An evolved status is reserved for system reconciliation")
    previous_state = situation.state
    if payload.state == previous_state:
        raise HTTPException(status_code=409, detail="This care situation already has that status")

    transitions = {
        SituationState.ACTIVE: {
            SituationState.WORSENING, SituationState.IMPROVING,
            SituationState.RESOLVED, SituationState.UNKNOWN,
        },
        SituationState.WORSENING: {
            SituationState.ACTIVE, SituationState.IMPROVING,
            SituationState.RESOLVED, SituationState.UNKNOWN,
        },
        SituationState.IMPROVING: {
            SituationState.ACTIVE, SituationState.WORSENING,
            SituationState.RESOLVED, SituationState.UNKNOWN,
        },
        SituationState.UNKNOWN: {
            SituationState.ACTIVE, SituationState.WORSENING,
            SituationState.IMPROVING, SituationState.RESOLVED,
        },
        SituationState.RESOLVED: {SituationState.ACTIVE},
        SituationState.EVOLVED: set(),
    }
    if payload.state not in transitions.get(previous_state, set()):
        raise HTTPException(status_code=409, detail="This care situation cannot move to that status")

    evidence_ids = list(dict.fromkeys(value.strip() for value in payload.evidence_ids if value and value.strip()))
    if not evidence_ids:
        raise HTTPException(status_code=422, detail="A situation status change must reference saved source records")

    valid_ids = set()
    for model in (CareEvent, Evidence, CareAssertion, EvidenceObservation):
        valid_ids.update(
            row[0] for row in db.query(model.id).filter(
                model.person_id == person_id,
                model.id.in_(evidence_ids),
            ).all()
        )
    if valid_ids != set(evidence_ids):
        raise HTTPException(status_code=422, detail="Every source record must belong to this care record")

    related_source_ids = set(situation.signal_ids or [])
    linked_change_ids = list(situation.change_ids or [])
    if linked_change_ids:
        linked_changes = db.query(Change).filter(
            Change.person_id == person_id,
            Change.id.in_(linked_change_ids),
        ).all()
        for change in linked_changes:
            related_source_ids.update(change.evidence_ids or [])

    linked_pattern_ids = list(situation.pattern_ids or [])
    if linked_pattern_ids:
        linked_patterns = db.query(Pattern).filter(
            Pattern.person_id == person_id,
            Pattern.id.in_(linked_pattern_ids),
        ).all()
        linked_pattern_change_ids = set()
        for pattern in linked_patterns:
            related_source_ids.update(pattern.event_ids or [])
            linked_pattern_change_ids.update(pattern.change_ids or [])
        if linked_pattern_change_ids:
            pattern_changes = db.query(Change).filter(
                Change.person_id == person_id,
                Change.id.in_(linked_pattern_change_ids),
            ).all()
            for change in pattern_changes:
                related_source_ids.update(change.evidence_ids or [])

    if not set(evidence_ids).issubset(related_source_ids):
        raise HTTPException(
            status_code=422,
            detail="Resolution and trajectory decisions must use records linked to this care situation",
        )

    reason = payload.reason.strip()
    if not reason:
        raise HTTPException(status_code=422, detail="A non-empty reason is required for a situation status change")

    now = datetime.now(timezone.utc)
    context = dict(situation.context) if isinstance(situation.context, dict) else {}
    history = list(context.get("state_history") or [])
    history.append({
        "previous_state": previous_state.value,
        "new_state": payload.state.value,
        "actor_caregiver_id": session.caregiver_id,
        "reason": reason,
        "evidence_ids": evidence_ids,
        "recorded_at": now.isoformat(),
        "source": "caregiver_confirmed",
    })
    context["state_history"] = history[-100:]
    situation.context = context
    situation.state = payload.state

    try:
        db.commit()
        db.refresh(situation)
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=503, detail="The care situation status could not be saved") from exc

    return SituationStateTransitionResponse(
        situation=situation,
        previous_state=previous_state,
        new_state=payload.state,
        actor_caregiver_id=session.caregiver_id,
        reason=reason,
        evidence_ids=evidence_ids,
        recorded_at=now,
    )


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

def _coverage_record_out(row) -> dict:
    """Serialize UTC-normalized coverage timestamps safely across DB dialects."""
    def utc(value):
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    return {
        "id": row.id,
        "person_id": row.person_id,
        "subject_type": row.subject_type,
        "window_start": utc(row.window_start),
        "window_end": utc(row.window_end),
        "coverage_status": row.coverage_status,
        "source_evidence_ids": row.source_evidence_ids or [],
        "rationale": row.rationale,
        "recorded_by_caregiver_id": row.recorded_by_caregiver_id,
        "created_at": utc(row.created_at),
    }

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
        # Normalize offset-bearing API timestamps before persistence. SQLite
        # drops timezone metadata for DateTime columns, so stored naive values
        # must already represent UTC to make round-trip serialization defensible.
        window_start=payload.window_start.astimezone(timezone.utc),
        window_end=payload.window_end.astimezone(timezone.utc),
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
    return _coverage_record_out(row)


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
