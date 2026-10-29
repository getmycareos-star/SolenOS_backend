from datetime import datetime, timedelta, timezone
from typing import List, Optional
from sqlalchemy.orm import Session
from app.models.change import Change, Pattern
from app.models.care import CareEvent
from app.core.change_enums import PatternType, PatternStrength, ChangeDirection, ChangeType


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
    changes = changes_query.order_by(Change.created_at.asc()).all()

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
    source_events = event_query.order_by(CareEvent.occurred_at.asc(), CareEvent.id.asc()).all()

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
        )
        if pattern:
            patterns.append(pattern)

    comparable_changes = [
        change for change in changes
        if change.magnitude is not None
        and change.comparison_window_start is not None
        and change.comparison_window_end is not None
    ]
    if len(comparable_changes) >= 3:
        pattern = _build_trend_pattern(
            db=db,
            person_id=person_id,
            subject_type=subject_type,
            subject_id=subject_id or "",
            changes=comparable_changes,
            now=now,
        )
        if pattern:
            patterns.append(pattern)
    if len(changes) >= 2:
        pattern = _build_instability_pattern(
            db=db,
            person_id=person_id,
            subject_type=subject_type,
            subject_id=subject_id or "",
            changes=changes,
            now=now,
        )
        if pattern:
            patterns.append(pattern)
    return patterns


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
        candidate.superseded_by_pattern_id = pattern.id
    if existing:
        db.flush()
    db.add(pattern)
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
        alternative_interpretations=[
            "Rates describe distinct linked records; observation coverage is not measured."
        ],
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
    if len(changes) < 3:
        return None
    changes_with_magnitude = [c for c in changes if c.magnitude is not None]
    if len(changes_with_magnitude) < 3:
        return None
    magnitudes = [c.magnitude for c in changes_with_magnitude]
    if all(magnitudes[i] <= magnitudes[i + 1] for i in range(len(magnitudes) - 1)):
        direction = ChangeDirection.INCREASING
    elif all(magnitudes[i] >= magnitudes[i + 1] for i in range(len(magnitudes) - 1)):
        direction = ChangeDirection.DECREASING
    else:
        return None
    event_ids = []
    for c in changes:
        event_ids.extend(c.evidence_ids or [])
    event_ids = sorted(set(event_ids))
    temporal_start = min(c.created_at for c in changes)
    temporal_end = max(c.created_at for c in changes)
    pattern = Pattern(
        person_id=person_id,
        subject_type=subject_type,
        subject_id=subject_id,
        pattern_type=PatternType.TREND,
        pattern_strength=PatternStrength.ESTABLISHED,
        event_ids=event_ids,
        change_ids=[c.id for c in changes],
        temporal_start=temporal_start,
        temporal_end=temporal_end,
        direction=direction,
        confidence=None,
        evidence_count=len(event_ids),
        alternative_interpretations=[],
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
    if len(changes) < 2:
        return None
    has_increase = any(c.change_direction == ChangeDirection.INCREASING for c in changes)
    has_decrease = any(c.change_direction == ChangeDirection.DECREASING for c in changes)
    if not (has_increase and has_decrease):
        return None
    event_ids = []
    for c in changes:
        event_ids.extend(c.evidence_ids or [])
    event_ids = sorted(set(event_ids))
    temporal_start = min(c.created_at for c in changes)
    temporal_end = max(c.created_at for c in changes)
    pattern = Pattern(
        person_id=person_id,
        subject_type=subject_type,
        subject_id=subject_id,
        pattern_type=PatternType.INSTABILITY,
        pattern_strength=PatternStrength.EMERGING,
        event_ids=event_ids,
        change_ids=[c.id for c in changes],
        temporal_start=temporal_start,
        temporal_end=temporal_end,
        direction=ChangeDirection.FLUCTUATING,
        confidence=None,
        evidence_count=len(event_ids),
        alternative_interpretations=[],
    )
    return _persist_pattern(db, pattern)
