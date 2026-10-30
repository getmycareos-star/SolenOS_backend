from datetime import datetime, timedelta, timezone
from typing import List, Optional, Dict, Any
from sqlalchemy.orm import Session
from app.models.care import CareEvent, Evidence
from app.models.change import Change, Pattern, Situation
from app.models.uncertainty import (
    Contradiction as ContradictionModel,
    InformationGap as InformationGapModel,
    OpenQuestion as OpenQuestionModel,
)
from app.models.significance import SignificanceAssessment
from app.services.change_detection import detect_event_changes
from app.services.pattern_detection import detect_patterns
from app.core.change_enums import (
    SituationType,
    SituationState,
    PatternType,
    ChangeDirection,
    SignificanceVerdict,
    EvidenceStatus,
)
from app.core.uncertainty_enums import GapLifecycleStatus


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def detect_situations(
    db: Session,
    person_id: str,
    reference_time: Optional[datetime] = None,
) -> List[Situation]:
    now = _ensure_utc(reference_time) or _utc_now()
    event_types = _get_recent_event_types(db, person_id, now)
    all_changes: List[Change] = []
    all_patterns: List[Pattern] = []
    for event_type in event_types:
        changes = detect_event_changes(db, person_id, event_type, None, now)
        all_changes.extend(changes)
        patterns = detect_patterns(db, person_id, event_type, None, now)
        all_patterns.extend(patterns)
    if not all_changes and not all_patterns:
        return []

    situations = []
    meaningful_changes = [c for c in all_changes if _is_meaningful(c)]
    if meaningful_changes:
        change_situation = _form_change_situation(
            db=db,
            person_id=person_id,
            changes=meaningful_changes,
            patterns=all_patterns,
            now=now,
        )
        if change_situation:
            situations.append(change_situation)
    if len(all_patterns) >= 2:
        pattern_situation = _form_pattern_situation(
            db=db,
            person_id=person_id,
            patterns=all_patterns,
            changes=all_changes,
            now=now,
        )
        if pattern_situation:
            situations.append(pattern_situation)
    for pattern in all_patterns:
        if pattern.pattern_type == PatternType.FREQUENCY_INCREASE:
            situation = _form_frequency_situation(
                db=db,
                person_id=person_id,
                pattern=pattern,
                changes=[c for c in all_changes if c.subject_type == pattern.subject_type],
                now=now,
            )
            if situation:
                situations.append(situation)
    return situations


def _is_meaningful(change: Change) -> bool:
    return change.significance_verdict == SignificanceVerdict.MEANINGFUL_CANDIDATE


def _get_recent_event_types(db: Session, person_id: str, now: datetime) -> List[str]:
    thirty_days_ago = now - timedelta(days=30)
    events = db.query(CareEvent).filter(
        CareEvent.person_id == person_id,
        CareEvent.occurred_at >= thirty_days_ago,
    ).all()
    event_types = list(set(e.event_type for e in events))
    return event_types


def _form_change_situation(
    db: Session,
    person_id: str,
    changes: List[Change],
    patterns: List[Pattern],
    now: datetime,
) -> Optional[Situation]:
    if not changes:
        return None
    signal_ids = []
    signal_types = []
    for c in changes:
        signal_ids.extend(c.evidence_ids or [])
        signal_types.append(c.subject_type)
    signal_ids = list(set(signal_ids))
    signal_types = list(set(signal_types))
    # Current Change rows carry Evidence IDs; historical rows may carry
    # CareEvent IDs. Resolve both namespaces within this person's record.
    # Source observation time takes precedence over assessment/ingestion time.
    source_evidence = (
        db.query(Evidence)
        .filter(Evidence.person_id == person_id, Evidence.id.in_(signal_ids))
        .all()
        if signal_ids else []
    )
    source_events = (
        db.query(CareEvent)
        .filter(CareEvent.person_id == person_id, CareEvent.id.in_(signal_ids))
        .all()
        if signal_ids else []
    )
    source_times = [
        observed_at
        for observed_at in (
            [_ensure_utc(item.occurred_at) for item in source_evidence]
            + [_ensure_utc(item.occurred_at) for item in source_events]
        )
        if observed_at is not None
    ]
    if source_times:
        temporal_start = min(source_times)
        temporal_end = max(source_times)
    else:
        comparison_starts = [
            value for change in changes
            if (value := _ensure_utc(change.comparison_window_start)) is not None
        ]
        comparison_ends = [
            value for change in changes
            if (value := _ensure_utc(change.comparison_window_end)) is not None
        ]
        assessment_times = [
            value for change in changes
            if (value := _ensure_utc(change.created_at)) is not None
        ]
        temporal_start = min(comparison_starts or assessment_times or [now])
        temporal_end = max(comparison_ends or assessment_times or [now])
    directions = {c.change_direction for c in changes}
    has_increasing = ChangeDirection.INCREASING in directions
    has_decreasing = ChangeDirection.DECREASING in directions
    if ChangeDirection.FLUCTUATING in directions or (has_increasing and has_decreasing):
        # Mixed directional evidence is fluctuation, never a stable-state claim.
        situation_type = SituationType.FLUCTUATING_STATE
    elif directions == {ChangeDirection.STABLE}:
        situation_type = SituationType.STABLE_STATE
    elif has_increasing and "fall" in signal_types:
        situation_type = SituationType.FUNCTIONAL_INSTABILITY
    else:
        # A directional count change alone does not establish decline or recovery.
        situation_type = SituationType.UNKNOWN
    relevant_patterns = [p for p in patterns if p.subject_type in signal_types]
    significance_by_change = [
        (change, _primary_significance(db, change))
        for change in changes
    ]
    primary = next(
        (assessment for _, assessment in significance_by_change if assessment is not None),
        None,
    )
    # A persisted assessment is authoritative even when its candidate flag is
    # false. Fall back to the change-level hint only when no assessment exists.
    attention = any(
        assessment.attention_candidate
        if assessment is not None
        else _bool_none(change.attention_candidate)
        for change, assessment in significance_by_change
    )
    follow_up = any(
        assessment.follow_up_candidate
        if assessment is not None
        else _bool_none(change.follow_up_candidate)
        for change, assessment in significance_by_change
    )

    context = _build_situation_context(db, person_id, changes, primary)
    alternative_interpretations = _derive_alternatives(db, person_id, changes, patterns)
    situation = Situation(
        person_id=person_id,
        situation_type=situation_type,
        state=SituationState.ACTIVE,
        title=_generate_situation_title(changes),
        description=_generate_situation_description(changes, relevant_patterns),
        signal_ids=signal_ids,
        signal_types=signal_types,
        temporal_start=temporal_start,
        temporal_end=temporal_end,
        pattern_ids=[p.id for p in relevant_patterns],
        change_ids=[c.id for c in changes],
        significance_assessment_id=primary.id if primary else None,
        confidence=None,
        alternative_interpretations=alternative_interpretations,
        context=context,
        attention_candidate=attention,
        follow_up_candidate=follow_up,
        explanation=_build_situation_explanation(changes, relevant_patterns, attention, follow_up),
    )
    db.add(situation)
    db.flush()
    return situation


def _bool_none(v: Any) -> bool:
    return bool(v or False)


def _primary_significance(db: Session, change: Change) -> Optional[SignificanceAssessment]:
    if not change.significance_assessment_id:
        return None
    return (
        db.query(SignificanceAssessment)
        .filter(SignificanceAssessment.id == change.significance_assessment_id)
        .first()
    )


def _situation_confidence(changes: List[Change], patterns: List[Pattern]) -> Optional[float]:
    # These upstream scores are not calibrated; unknown must remain unknown.
    return None


def _build_situation_context(db: Session, person_id: str, changes: List[Change],
                             primary: Optional[SignificanceAssessment]) -> Dict[str, Any]:
    context: Dict[str, Any] = {
        "type": "situation_understanding",
        "significance_verdict": primary.verdict.value if primary else SignificanceVerdict.INSUFFICIENT_EVIDENCE.value,
        "significance_confidence": primary.significance_confidence if primary else 0.0,
        "evidence_confidence": primary.evidence_confidence if primary else 0.0,
        "evidence_status": primary.evidence_status.value if primary else EvidenceStatus.UNKNOWN.value,
        "dimensions": primary.dimensions if primary else [],
        "reporters": primary.reporters if primary else [],
        "possible_context": primary.possible_context if primary else [],
        "per_change_significance": {},
        "epistemic": {},
    }
    for c in changes:
        sig = _primary_significance(db, c)
        if sig:
            context["per_change_significance"][c.id] = {
                "verdict": sig.verdict.value,
                "significance_confidence": sig.significance_confidence,
                "evidence_status": sig.evidence_status.value,
                "attention_candidate": sig.attention_candidate,
                "follow_up_candidate": sig.follow_up_candidate,
                "baseline_id": sig.baseline_id,
            }
            if c.subject_type not in context["epistemic"]:
                context["epistemic"][c.subject_type] = _epistemic_context(db, person_id, c.subject_type, c.subject_id)
    return context


def _epistemic_context(db: Session, person_id: str, subject_type: str, subject_id: str) -> Dict[str, Any]:
    q = db.query(ContradictionModel).filter(
        ContradictionModel.person_id == person_id,
        ContradictionModel.subject_type == subject_type,
    )
    if subject_id:
        q = q.filter(ContradictionModel.subject_id == subject_id)
    contradictions = q.all()

    gaps = (
        db.query(InformationGapModel)
        .filter(
            InformationGapModel.person_id == person_id,
            InformationGapModel.subject_type == subject_type,
            InformationGapModel.lifecycle_status == GapLifecycleStatus.OPEN,
        )
        .all()
    )
    if subject_id:
        gaps = [g for g in gaps if g.subject_id == subject_id or g.subject_id == ""]

    questions = db.query(OpenQuestionModel).filter(
        OpenQuestionModel.person_id == person_id,
        OpenQuestionModel.subject_type == subject_type,
        OpenQuestionModel.status == "open",
    ).all()

    if contradictions:
        epistemic_state = "conflicting"
    elif not gaps and not questions:
        epistemic_state = "known_or_reported"
    elif gaps or questions:
        epistemic_state = "partially_known"
    else:
        epistemic_state = "unknown"

    return {
        "epistemic_state": epistemic_state,
        "contradictions": [{"id": c.id, "field": c.field, "description": c.description,
                            "evidence_ids": c.evidence_ids or [], "resolution_status": c.resolution_status} for c in contradictions],
        "open_gaps": [{"id": g.id, "field": g.field, "gap_reason": g.gap_reason.value if g.gap_reason else None} for g in gaps],
        "open_questions": [{"id": q.id, "question": q.question, "priority": q.priority.value if q.priority else None} for q in questions],
    }


def _derive_alternatives(db: Session, person_id: str, changes: List[Change],
                         patterns: List[Pattern]) -> List[Dict[str, Any]]:
    interpretations: List[Dict[str, Any]] = []
    for c in changes:
        ep = _epistemic_context(db, person_id, c.subject_type, c.subject_id)
        alt = {
            "interpretation": (
                f"Documentation or reporting change rather than actual change for {c.subject_type} "
                f"({c.change_type.value}, n={c.current_count})"
            ),
            "confidence": None,
            "supporting_evidence": "No documentation-density delta computed for this window; treat as unconfirmed hypothesis.",
        }
        if ep["contradictions"]:
            alt["interpretation"] += " Conflicting observations exist for this subject."
        interpretations.append(alt)
    for p in patterns:
        interpretations.append({
            "interpretation": f"Pattern {p.pattern_type.value} for {p.subject_type} may reflect recording cadence, not pathophysiology.",
            # Pattern strength is not a calibrated probability. Keep confidence
            # unknown until this inference is validated against outcome-labelled data.
            "confidence": None,
            "confidence_status": "not_calibrated",
            "supporting_evidence": f"Pattern strength {p.pattern_strength.value}; {p.evidence_count} evidence items.",
        })
    return interpretations


def _form_pattern_situation(
    db: Session,
    person_id: str,
    patterns: List[Pattern],
    changes: List[Change],
    now: datetime,
) -> Optional[Situation]:
    if len(patterns) < 2:
        return None
    active_patterns = [p for p in patterns if len(p.change_ids or []) >= 2 or len(p.event_ids or []) >= 2]
    if len(active_patterns) < 2:
        return None
    signal_ids = []
    signal_types = []
    for p in active_patterns:
        signal_ids.extend(p.event_ids or [])
        signal_types.append(p.subject_type)
    signal_ids = list(set(signal_ids))
    signal_types = list(set(signal_types))
    temporal_start = min(p.temporal_start for p in active_patterns)
    temporal_end = max(p.temporal_end for p in active_patterns)

    change_sigs = {c.id: _primary_significance(db, c) for c in changes}
    change_sigs = {k: v for k, v in change_sigs.items() if v is not None}
    att = any(s.attention_candidate for s in change_sigs.values())
    foll = any(s.follow_up_candidate for s in change_sigs.values())

    context = _build_converged_context(db, person_id, active_patterns, change_sigs)
    situation = Situation(
        person_id=person_id,
        situation_type=(
            SituationType.FUNCTIONAL_INSTABILITY
            if any("fall" in s for s in signal_types)
            else SituationType.FLUCTUATING_STATE
        ),
        state=SituationState.ACTIVE,
        title="Converging patterns detected",
        description="Multiple patterns detected across related signals.",
        signal_ids=signal_ids,
        signal_types=signal_types,
        temporal_start=temporal_start,
        temporal_end=temporal_end,
        pattern_ids=[p.id for p in active_patterns],
        change_ids=[c.id for c in changes],
        significance_assessment_id=(
            next((v.id for v in change_sigs.values()), None)
        ),
        confidence=None,
        alternative_interpretations=[
            {
                "interpretation": "Convergence may indicate a systemic change, or may reflect inconsistent reporting across signals.",
                "confidence": None,
                "supporting_evidence": f"{len(active_patterns)} active patterns.",
            }
        ],
        context=context,
        attention_candidate=att,
        follow_up_candidate=foll,
        explanation=(
            "Multiple patterns detected across related signals. Temporal association "
            "of events does not establish causation; verify with additional evidence."
        ),
    )
    db.add(situation)
    db.flush()
    return situation


def _build_converged_context(db: Session, person_id: str, patterns: List[Pattern],
                             change_sigs: Dict[str, SignificanceAssessment]) -> Dict[str, Any]:
    ctx: Dict[str, Any] = {
        "type": "converged_situation_understanding",
        "significance_verdict": SignificanceVerdict.MEANINGFUL_CANDIDATE.value,
        "significance_confidence": min(s.significance_confidence for s in change_sigs.values()) if change_sigs else None,
        "evidence_confidence": min(s.evidence_confidence for s in change_sigs.values()) if change_sigs else None,
        "evidence_status": max(
            (s.evidence_status for s in change_sigs.values()),
            key=lambda e: 0 if e == EvidenceStatus.CONFIRMED else
               1 if e == EvidenceStatus.REPORTED else
               2 if e == EvidenceStatus.INFERRED else
               3 if e == EvidenceStatus.UNKNOWN else 4,
        ).value if change_sigs else EvidenceStatus.UNKNOWN.value,
        "per_change_significance": {},
        "epistemic": {},
        "warnings": ["convergence is correlative, not causal"],
        "possible_context": ["shared temporal trigger is plausible but unverified"],
    }
    for cid, sig in change_sigs.items():
        ctx["per_change_significance"][cid] = {
            "verdict": sig.verdict.value,
            "significance_confidence": sig.significance_confidence,
            "evidence_status": sig.evidence_status.value,
        }
    for p in patterns:
        ctx["epistemic"].setdefault(p.subject_type, _epistemic_context(db, person_id, p.subject_type, p.subject_id))
    return ctx


def _form_frequency_situation(
    db: Session,
    person_id: str,
    pattern: Pattern,
    changes: List[Change],
    now: datetime,
) -> Optional[Situation]:
    change_sigs = {c.id: _primary_significance(db, c) for c in changes}
    change_sigs = {k: v for k, v in change_sigs.items() if v is not None}
    att = any(s.attention_candidate for s in change_sigs.values()) if change_sigs else False
    foll = any(s.follow_up_candidate for s in change_sigs.values()) if change_sigs else pattern.pattern_strength.value == "established"

    context = {
        "type": "frequency_situation_understanding",
        "significance_verdict": SignificanceVerdict.MEANINGFUL_CANDIDATE.value if att else SignificanceVerdict.NOT_MEANINGFUL.value,
        "significance_confidence": min(s.significance_confidence for s in change_sigs.values()) if change_sigs else None,
        "evidence_confidence": min(s.evidence_confidence for s in change_sigs.values()) if change_sigs else None,
        "evidence_status": EvidenceStatus.REPORTED.value,
        "per_change_significance": {
            cid: {"verdict": s.verdict.value, "significance_confidence": s.significance_confidence}
            for cid, s in change_sigs.items()
        },
        "epistemic": _epistemic_context(db, person_id, pattern.subject_type, pattern.subject_id) if changes else {},
        "possible_context": ["frequency increase may reflect reporting cadence, not true event frequency"],
        "warnings": [],
    }
    situation = Situation(
        person_id=person_id,
        situation_type=(
            SituationType.FUNCTIONAL_INSTABILITY
            if pattern.subject_type == "fall"
            else SituationType.FLUCTUATING_STATE
        ),
        state=SituationState.ACTIVE,
        title=f"Increasing {pattern.subject_type} frequency",
        description=(
            f"Frequency of {pattern.subject_type} events has increased. "
            f"Pattern strength: {pattern.pattern_strength.value}. "
            f"Rate: {pattern.rate_per_period:.2f} per period."
            if pattern.rate_per_period
            else f"Frequency of {pattern.subject_type} events has increased."
        ),
        signal_ids=list(set(pattern.event_ids or [])),
        signal_types=[pattern.subject_type],
        temporal_start=pattern.temporal_start,
        temporal_end=pattern.temporal_end,
        pattern_ids=[pattern.id],
        change_ids=[c.id for c in changes],
        significance_assessment_id=(next(iter(change_sigs.values()), None).id if change_sigs else None),
        confidence=None,
        alternative_interpretations=[
            {
                "interpretation": f"Increase may be documentation-driven for {pattern.subject_type}",
                # This is a competing hypothesis without a validated probability.
                "confidence": None,
                "confidence_status": "not_calibrated",
                "supporting_evidence": "No documentation-density comparison; sparse baseline.",
            }
        ],
        context=context,
        attention_candidate=att,
        follow_up_candidate=foll,
        explanation=(
            "Increasing event frequency is a longitudinal observation, not a prognosis. "
            "Rule out reporting-driven frequency change before acting."
        ),
    )
    db.add(situation)
    db.flush()
    return situation


def _generate_situation_title(changes: List[Change]) -> str:
    if not changes:
        return "Situation detected"
    significant = changes[0]
    return f"{significant.change_type.value}: {significant.subject_type}"


def _generate_situation_description(changes: List[Change], patterns: List[Pattern]) -> str:
    parts = []
    for c in changes:
        parts.append(c.explanation or f"{c.change_type.value}: {c.subject_type}")
    for p in patterns:
        parts.append(f"Pattern: {p.pattern_type.value} ({p.pattern_strength.value})")
    return " | ".join(parts)


def _build_situation_explanation(changes: List[Change], patterns: List[Pattern],
                                 attention: bool, follow_up: bool) -> str:
    parts = []
    parts.append(
        "Situation constructed from evidence-grounded change(s); temporal association is not causation."
    )
    if attention:
        parts.append("Attention candidate: warrants monitoring.")
    if follow_up:
        parts.append("Follow-up candidate: worth clarifying with a professional.")
    parts.append("This interpretation is reversible when underlying evidence changes.")
    return " ".join(parts)


def update_situations(
    db: Session,
    person_id: str,
    new_event: CareEvent,
    reference_time: Optional[datetime] = None,
) -> List[Situation]:
    now = _ensure_utc(reference_time) or _utc_now()
    active_situations = db.query(Situation).filter(
        Situation.person_id == person_id,
        Situation.is_active,
        Situation.state == SituationState.ACTIVE,
    ).all()
    if not active_situations:
        return []
    updated = []
    for situation in active_situations:
        if new_event.event_type not in (situation.signal_types or []):
            continue

        # Change situations use evidence IDs in signal_ids. Keep the event
        # identity in a separate context list so an event without an attached
        # evidence upload can still advance the situation without mixing ID types.
        context = dict(situation.context or {})
        context_type = context.get("type")
        if context_type == "situation_understanding":
            incoming_ids = list(new_event.evidence_ids or [])
            related_event_ids = list(context.get("event_ids") or [])
            event_reference_added = new_event.id not in related_event_ids
            if event_reference_added:
                related_event_ids.append(new_event.id)
                context["event_ids"] = related_event_ids
        elif context_type in {
            "converged_situation_understanding",
            "frequency_situation_understanding",
        }:
            incoming_ids = [new_event.id]
            event_reference_added = False
        else:
            # Legacy rows predate the context discriminator. Infer the ID
            # namespace only from persisted, same-person records. If the IDs
            # resolve in both namespaces, the row is ambiguous and must not
            # be mutated automatically.
            existing_ids = list(situation.signal_ids or [])
            evidence_matches = (
                db.query(Evidence.id)
                .filter(Evidence.person_id == person_id, Evidence.id.in_(existing_ids))
                .all()
                if existing_ids else []
            )
            event_matches = (
                db.query(CareEvent.id)
                .filter(CareEvent.person_id == person_id, CareEvent.id.in_(existing_ids))
                .all()
                if existing_ids else []
            )
            if evidence_matches and event_matches:
                continue
            if evidence_matches:
                incoming_ids = list(new_event.evidence_ids or [])
            elif event_matches:
                incoming_ids = [new_event.id]
            else:
                # Empty or inaccessible legacy provenance is not enough to
                # infer the namespace. Preserve it for explicit reconciliation.
                continue

        if context_type != "situation_understanding":
            existing_ids = list(situation.signal_ids or [])
        additions = [item for item in incoming_ids if item and item not in existing_ids]
        if not additions and not event_reference_added:
            continue

        if additions:
            situation.signal_ids = existing_ids + additions
        if context_type == "situation_understanding" and event_reference_added:
            situation.context = context

        observed_at = _ensure_utc(new_event.occurred_at)
        current_end = _ensure_utc(situation.temporal_end)
        if observed_at is not None and (current_end is None or observed_at > current_end):
            situation.temporal_end = observed_at
        situation.explanation = (
            (situation.explanation or "")
            + f" Updated with event {new_event.id} observed at "
            f"{observed_at.isoformat() if observed_at else 'an unknown time'}."
        )
        updated.append(situation)
    if updated:
        db.commit()
    return updated
