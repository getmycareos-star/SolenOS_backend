from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional
from sqlalchemy.orm import Session
from app.models.change import Change, Pattern
from app.models.care import CareEvent
from app.core.change_enums import PatternType, PatternStrength, ChangeDirection, ChangeType

MAX_PATTERN_SOURCE_EVENTS = 1000
MAX_PATTERN_CHANGE_ASSESSMENTS = 500


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def detect_patterns(
    db: Session,
    person_id: str,
    subject_type: str,
    subject_id: Optional[str],
    reference_time: Optional[datetime] = None,
) -> List[Pattern]:
    """Detect recurrence from persisted source events, not repeated assessment rows."""
    now = _ensure_utc(datetime.now(timezone.utc) if reference_time is None else reference_time)
    window_start = now - timedelta(days=90)
    changes_query = db.query(Change).filter(
        Change.person_id == person_id,
        Change.subject_type == subject_type,
        Change.is_active.is_(True),
    )
    if subject_id:
        changes_query = changes_query.filter(Change.subject_id == subject_id)
    queried_changes = (
        changes_query
        .order_by(Change.created_at.desc(), Change.id.asc())
        .limit(MAX_PATTERN_CHANGE_ASSESSMENTS + 1)
        .all()
    )
    changes_truncated = len(queried_changes) > MAX_PATTERN_CHANGE_ASSESSMENTS
    changes = queried_changes[:MAX_PATTERN_CHANGE_ASSESSMENTS]
    changes.reverse()

    event_query = db.query(CareEvent).filter(
        CareEvent.person_id == person_id,
        CareEvent.event_type == subject_type,
        CareEvent.status.notin_(("invalidated", "superseded", "unparsed_raw")),
        CareEvent.occurred_at >= window_start,
        CareEvent.occurred_at <= now,
    )
    if subject_id:
        linked_ids = sorted({
            evidence_id
            for change in changes
            for evidence_id in (change.evidence_ids or [])
        })
        if not linked_ids:
            return []
        event_query = event_query.filter(CareEvent.id.in_(linked_ids))
    queried_events = (
        event_query
        .order_by(CareEvent.occurred_at.desc(), CareEvent.id.asc())
        .limit(MAX_PATTERN_SOURCE_EVENTS + 1)
        .all()
    )
    events_truncated = len(queried_events) > MAX_PATTERN_SOURCE_EVENTS
    source_events = queried_events[:MAX_PATTERN_SOURCE_EVENTS]
    source_events.sort(key=lambda event: (_ensure_utc(event.occurred_at), event.id))

    canonical_events = []
    seen_identities = set()
    for event in source_events:
        evidence_signature = tuple(sorted(set(event.evidence_ids or [])))
        identity = ("evidence", evidence_signature) if evidence_signature else ("event", event.id)
        if identity in seen_identities:
            continue
        seen_identities.add(identity)
        canonical_events.append(event)

    patterns: List[Pattern] = []
    if len(canonical_events) >= 2:
        pattern = _build_recurrence_pattern(
            db=db,
            person_id=person_id,
            subject_type=subject_type,
            subject_id=subject_id or "",
            events=canonical_events,
            changes=changes,
            now=now,
            history_truncated=events_truncated,
        )
        if pattern:
            patterns.append(pattern)

    # Never combine assessments for different subjects into one trend or
    # instability sequence. When the caller does not scope a subject, reconcile
    # each persisted subject independently.
    change_groups: Dict[str, List[Change]] = {}
    for change in changes:
        group_subject_id = change.subject_id or ""
        change_groups.setdefault(group_subject_id, []).append(change)

    for group_subject_id, group_changes in change_groups.items():
        comparable_changes = [
            change for change in group_changes
            if change.magnitude is not None
            and change.comparison_window_start is not None
            and change.comparison_window_end is not None
        ]
        if len(comparable_changes) >= 3:
            pattern = _build_trend_pattern(
                db=db,
                person_id=person_id,
                subject_type=subject_type,
                subject_id=group_subject_id,
                changes=comparable_changes,
                now=now,
            )
            if pattern:
                if changes_truncated:
                    _append_limit_note(
                        db,
                        pattern,
                        "Change assessment history was bounded to the most recent "
                        f"{MAX_PATTERN_CHANGE_ASSESSMENTS} records; earlier assessments were not included.",
                    )
                patterns.append(pattern)
        if len(group_changes) >= 2:
            pattern = _build_instability_pattern(
                db=db,
                person_id=person_id,
                subject_type=subject_type,
                subject_id=group_subject_id,
                changes=group_changes,
                now=now,
            )
            if pattern:
                if changes_truncated:
                    _append_limit_note(
                        db,
                        pattern,
                        "Change assessment history was bounded to the most recent "
                        f"{MAX_PATTERN_CHANGE_ASSESSMENTS} records; earlier assessments were not included.",
                    )
                patterns.append(pattern)
    return patterns


def _append_limit_note(db: Session, pattern: Pattern, note: str) -> None:
    alternatives = list(pattern.alternative_interpretations or [])
    if note not in alternatives:
        alternatives.append(note)
        pattern.alternative_interpretations = alternatives
        db.flush()


def _validated_source_event_ids(
    db: Session,
    person_id: str,
    subject_type: str,
    candidate_ids: List[str],
    reference_time: datetime,
) -> List[str]:
    """Only treat real, same-person source events as pattern evidence."""
    unique_ids = sorted(set(candidate_ids))
    if not unique_ids:
        return []
    rows = (
        db.query(CareEvent.id)
        .filter(
            CareEvent.id.in_(unique_ids),
            CareEvent.person_id == person_id,
            CareEvent.event_type == subject_type,
            CareEvent.status.notin_(("invalidated", "superseded", "unparsed_raw")),
            CareEvent.occurred_at <= reference_time,
        )
        .all()
    )
    return sorted(row[0] for row in rows)


def _persist_pattern(db: Session, pattern: Pattern) -> Pattern:
    """Return an identical active pattern or preserve history through supersession."""
    existing = (
        db.query(Pattern)
        .filter(
            Pattern.person_id == pattern.person_id,
            Pattern.subject_type == pattern.subject_type,
            Pattern.subject_id == pattern.subject_id,
            Pattern.pattern_type == pattern.pattern_type,
            Pattern.is_active.is_(True),
        )
        .with_for_update()
        .order_by(Pattern.created_at.desc())
        .all()
    )
    for candidate in existing:
        same_window = (
            _ensure_utc(candidate.temporal_start) == _ensure_utc(pattern.temporal_start)
            and _ensure_utc(candidate.temporal_end) == _ensure_utc(pattern.temporal_end)
        )
        if (
            set(candidate.event_ids or []) == set(pattern.event_ids or [])
            and set(candidate.change_ids or []) == set(pattern.change_ids or [])
            and same_window
        ):
            return candidate

    # Release the active-key uniqueness constraint before inserting its
    # replacement; the surrounding request transaction keeps both changes atomic.
    for candidate in existing:
        candidate.is_active = False
    if existing:
        db.flush()
    db.add(pattern)
    db.flush()
    for candidate in existing:
        candidate.superseded_by_pattern_id = pattern.id
    if existing:
        db.flush()
    return pattern


def _build_recurrence_pattern(
    db: Session,
    person_id: str,
    subject_type: str,
    subject_id: str,
    events: List[CareEvent],
    changes: List[Change],
    now: datetime,
    history_truncated: bool = False,
) -> Optional[Pattern]:
    ordered_events = sorted(
        events,
        key=lambda event: (_ensure_utc(event.occurred_at), event.id),
    )
    timestamps = [_ensure_utc(event.occurred_at) for event in ordered_events]
    distinct_times = sorted({timestamp for timestamp in timestamps if timestamp is not None})
    if len(ordered_events) < 2 or len(distinct_times) < 2:
        return None
    span_days = (distinct_times[-1] - distinct_times[0]).total_seconds() / 86400.0
    if span_days <= 0:
        return None

    event_ids = [event.id for event in ordered_events]
    event_id_set = set(event_ids)
    linked_changes = [
        change for change in changes
        if event_id_set.intersection(change.evidence_ids or [])
    ]
    change_ids = sorted({change.id for change in linked_changes})
    frequency_per_day = len(ordered_events) / span_days
    rate_per_30_days = len(ordered_events) / (span_days / 30.0)
    alternatives = [
        "Rates describe distinct linked records; observation coverage is not measured."
    ]
    if history_truncated:
        alternatives.append(
            f"Source event history was bounded to the most recent {MAX_PATTERN_SOURCE_EVENTS} records; earlier events were not included."
        )
    pattern = Pattern(
        person_id=person_id,
        subject_type=subject_type,
        subject_id=subject_id,
        pattern_type=PatternType.RECURRENCE,
        pattern_strength=PatternStrength.ESTABLISHED if len(ordered_events) >= 3 else PatternStrength.EMERGING,
        event_ids=event_ids,
        change_ids=change_ids,
        temporal_start=distinct_times[0],
        temporal_end=distinct_times[-1],
        frequency=frequency_per_day,
        rate_per_period=rate_per_30_days,
        direction=ChangeDirection.UNKNOWN,
        confidence=None,
        evidence_count=len(event_ids),
        alternative_interpretations=alternatives,
    )
    return _persist_pattern(db, pattern)


def _build_trend_pattern(
    db: Session,
    person_id: str,
    subject_type: str,
    subject_id: str,
    changes: List[Change],
    now: datetime,
) -> Optional[Pattern]:
    """Detect a direction-consistent sequence only from comparable, evidenced windows."""
    comparable = [
        change for change in changes
        if change.magnitude is not None
        and change.comparison_window_start is not None
        and change.comparison_window_end is not None
        and change.change_direction in (ChangeDirection.INCREASING, ChangeDirection.DECREASING)
    ]
    if len(comparable) < 3:
        return None

    directions = {change.change_direction for change in comparable}
    if len(directions) != 1:
        return None

    ordered = sorted(
        comparable,
        key=lambda change: (
            _ensure_utc(change.comparison_window_end),
            change.id,
        ),
    )
    window_ends = [_ensure_utc(change.comparison_window_end) for change in ordered]
    if len(set(window_ends)) < 3:
        return None

    # A trend without source evidence is not an evidence-backed pattern.
    event_ids = _validated_source_event_ids(
        db=db,
        person_id=person_id,
        subject_type=subject_type,
        candidate_ids=[
            evidence_id
            for change in ordered
            for evidence_id in (change.evidence_ids or [])
        ],
        reference_time=_ensure_utc(now),
    )
    if len(event_ids) < 3:
        return None

    change_ids = [change.id for change in ordered]
    temporal_start = min(_ensure_utc(change.comparison_window_start) for change in ordered)
    temporal_end = max(_ensure_utc(change.comparison_window_end) for change in ordered)
    overlapping_windows = any(
        _ensure_utc(ordered[index].comparison_window_end)
        > _ensure_utc(ordered[index + 1].comparison_window_start)
        for index in range(len(ordered) - 1)
    )
    alternatives = []
    if overlapping_windows:
        alternatives.append(
            "Comparison windows overlap; successive assessments are not statistically independent."
        )

    pattern = Pattern(
        person_id=person_id,
        subject_type=subject_type,
        subject_id=subject_id,
        pattern_type=PatternType.TREND,
        pattern_strength=PatternStrength.ESTABLISHED if len(ordered) >= 5 else PatternStrength.EMERGING,
        event_ids=event_ids,
        change_ids=change_ids,
        temporal_start=temporal_start,
        temporal_end=temporal_end,
        direction=next(iter(directions)),
        confidence=None,
        evidence_count=len(event_ids),
        alternative_interpretations=alternatives,
    )
    return _persist_pattern(db, pattern)


def _build_instability_pattern(
    db: Session,
    person_id: str,
    subject_type: str,
    subject_id: str,
    changes: List[Change],
    now: datetime,
) -> Optional[Pattern]:
    comparable = [
        change for change in changes
        if change.comparison_window_start is not None
        and change.comparison_window_end is not None
        and change.change_direction in (ChangeDirection.INCREASING, ChangeDirection.DECREASING)
    ]
    if len(comparable) < 2:
        return None
    has_increase = any(c.change_direction == ChangeDirection.INCREASING for c in comparable)
    has_decrease = any(c.change_direction == ChangeDirection.DECREASING for c in comparable)
    if not (has_increase and has_decrease):
        return None

    ordered = sorted(
        comparable,
        key=lambda change: (_ensure_utc(change.comparison_window_end), change.id),
    )
    if len({_ensure_utc(change.comparison_window_end) for change in ordered}) < 2:
        return None
    event_ids = _validated_source_event_ids(
        db=db,
        person_id=person_id,
        subject_type=subject_type,
        candidate_ids=[
            evidence_id
            for change in ordered
            for evidence_id in (change.evidence_ids or [])
        ],
        reference_time=_ensure_utc(now),
    )
    if len(event_ids) < 2:
        return None
    change_ids = [change.id for change in ordered]
    temporal_start = min(_ensure_utc(change.comparison_window_start) for change in ordered)
    temporal_end = max(_ensure_utc(change.comparison_window_end) for change in ordered)
    alternatives = [
        "Direction changes across source-linked assessments; this does not establish cause or clinical fluctuation."
    ]
    pattern = Pattern(
        person_id=person_id,
        subject_type=subject_type,
        subject_id=subject_id,
        pattern_type=PatternType.INSTABILITY,
        pattern_strength=PatternStrength.EMERGING,
        event_ids=event_ids,
        change_ids=change_ids,
        temporal_start=temporal_start,
        temporal_end=temporal_end,
        direction=ChangeDirection.FLUCTUATING,
        confidence=None,
        evidence_count=len(event_ids),
        alternative_interpretations=alternatives,
    )
    return _persist_pattern(db, pattern)
