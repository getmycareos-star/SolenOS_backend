from datetime import datetime, timezone
from typing import List, Optional
from sqlalchemy.orm import Session
from app.models.change import Change, Pattern
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
    now = _ensure_utc(datetime.now(timezone.utc) if reference_time is None else reference_time)
    query = db.query(Change).filter(
        Change.person_id == person_id,
        Change.subject_type == subject_type,
        Change.is_active,
    )
    if subject_id:
        query = query.filter(Change.subject_id == subject_id)
    changes = query.order_by(Change.created_at.asc()).all()
    if not changes:
        return []
    patterns = []
    frequency_changes = [c for c in changes if c.change_type in (
        ChangeType.ONSET,
        ChangeType.INCREASE,
        ChangeType.RECURRENCE,
    )]
    if len(frequency_changes) >= 2:
        pattern = _build_recurrence_pattern(
            db=db,
            person_id=person_id,
            subject_type=subject_type,
            subject_id=subject_id or "",
            changes=frequency_changes,
            now=now,
        )
        if pattern:
            patterns.append(pattern)
    if len(changes) >= 3:
        pattern = _build_trend_pattern(
            db=db,
            person_id=person_id,
            subject_type=subject_type,
            subject_id=subject_id or "",
            changes=changes,
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


def _build_recurrence_pattern(
    db: Session,
    person_id: str,
    subject_type: str,
    subject_id: str,
    changes: List[Change],
    now: datetime,
) -> Optional[Pattern]:
    recent_changes = [c for c in changes if (now - _ensure_utc(c.created_at)).days <= 90]
    if len(recent_changes) < 2:
        return None
    event_ids = []
    for c in recent_changes:
        event_ids.extend(c.evidence_ids or [])
    event_ids = list(set(event_ids))
    temporal_start = min(c.created_at for c in recent_changes)
    temporal_end = max(c.created_at for c in recent_changes)
    frequency = len(recent_changes) / 90.0
    direction = ChangeDirection.INCREASING
    if all(c.change_direction == ChangeDirection.INCREASING for c in recent_changes):
        pattern_type = PatternType.FREQUENCY_INCREASE
    elif all(c.change_direction == ChangeDirection.DECREASING for c in recent_changes):
        pattern_type = PatternType.FREQUENCY_DECREASE
        direction = ChangeDirection.DECREASING
    else:
        pattern_type = PatternType.RECURRENCE
    strength = PatternStrength.ESTABLISHED if len(recent_changes) >= 3 else PatternStrength.EMERGING
    confidence = 0.7 if len(recent_changes) >= 3 else 0.5
    pattern = Pattern(
        person_id=person_id,
        subject_type=subject_type,
        subject_id=subject_id,
        pattern_type=pattern_type,
        pattern_strength=strength,
        event_ids=event_ids,
        change_ids=[c.id for c in recent_changes],
        temporal_start=temporal_start,
        temporal_end=temporal_end,
        frequency=frequency,
        rate_per_period=frequency * 30,
        direction=direction,
        confidence=confidence,
        evidence_count=len(event_ids),
        alternative_interpretations=[],
    )
    db.add(pattern)
    db.flush()
    return pattern


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
    event_ids = list(set(event_ids))
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
        confidence=0.6,
        evidence_count=len(event_ids),
        alternative_interpretations=[],
    )
    db.add(pattern)
    db.flush()
    return pattern


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
    event_ids = list(set(event_ids))
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
        confidence=0.5,
        evidence_count=len(event_ids),
        alternative_interpretations=[],
    )
    db.add(pattern)
    db.flush()
    return pattern
