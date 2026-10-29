from datetime import datetime, timedelta, timezone
from statistics import mean, median, pstdev
from collections import Counter
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from typing import Dict, List, Optional
from sqlalchemy.orm import Session
from app.models.change import Change, Pattern
from app.models.care import CareEvent
from app.core.change_enums import PatternType, PatternStrength, ChangeDirection, ChangeType
from app.services.observation_coverage import assess_observation_coverage

MAX_PATTERN_SOURCE_EVENTS = 1000
MAX_PATTERN_CHANGE_ASSESSMENTS = 500
# Keep a bounded but genuinely longitudinal source window. Ninety days hid
# otherwise valid recurrence across longer care histories.
MAX_PATTERN_LOOKBACK_DAYS = 365


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
    window_start = now - timedelta(days=MAX_PATTERN_LOOKBACK_DAYS)
    # Superseded assessments are historical observations, not disposable rows.
    # Include them in longitudinal analysis while excluding explicitly rejected
    # or inconclusive assessments from pattern evidence.
    changes_query = db.query(Change).filter(
        Change.person_id == person_id,
        Change.subject_type == subject_type,
        (Change.confirmation_status.is_(None))
        | (Change.confirmation_status.notin_(("rejected", "inconclusive"))),
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
        # For subject-scoped assessments, the persisted Change rows identify
        # the exact source CareEvent IDs eligible for this subject. subject_id
        # is not the same thing as CareEvent.situation_id; do not conflate them.
        linked_event_ids = sorted({
            event_id
            for change in changes
            for event_id in (change.evidence_ids or [])
        })
        # An empty linkage set means no eligible events for this subject. Keep
        # the query empty so stale patterns can be retired when their evidence
        # was invalidated or no longer supports the assessment.
        event_query = event_query.filter(CareEvent.id.in_(linked_event_ids))
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
        if evidence_signature:
            # The same source may document several occurrences. Collapse only
            # copies anchored to the same evidence set and occurrence timestamp;
            # narrative title differences introduced by importers must not inflate
            # recurrence counts, while differently dated events remain distinct.
            identity = (
                "evidence-backed-occurrence",
                evidence_signature,
                _ensure_utc(event.occurred_at),
            )
        else:
            # Without a shared source anchor, retain each event as a distinct
            # occurrence rather than guessing that similar records are duplicates.
            identity = ("event", event.id)
        if identity in seen_identities:
            continue
        seen_identities.add(identity)
        canonical_events.append(event)

    patterns: List[Pattern] = []
    # Frequency comparisons are emitted only when both adjacent 30-day windows
    # have explicit complete observation coverage. They remain descriptive
    # candidates, not proof of cause or clinical significance.
    # A truncated source-event query cannot supply exact denominators for
    # adjacent-window rates. Withhold frequency patterns rather than treating
    # the bounded subset as the full occurrence count.
    frequency_pattern = None
    if not events_truncated:
        frequency_pattern = _build_frequency_pattern(
            db=db,
            person_id=person_id,
            subject_type=subject_type,
            subject_id=subject_id or "",
            events=canonical_events,
            changes=changes,
            now=now,
        )
    if frequency_pattern:
        patterns.append(frequency_pattern)

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
        # A short-window cluster is distinct from recurrence across the full
        # history. It is based on source-event timestamps, never repeated
        # assessment rows or narrative keyword matches.
        cluster = _build_clustering_pattern(
            db=db,
            person_id=person_id,
            subject_type=subject_type,
            subject_id=subject_id or "",
            events=canonical_events,
        )
        if cluster:
            patterns.append(cluster)

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
            and change.baseline_coverage_status == "covered"
            and change.current_coverage_status == "covered"
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
        if len(comparable_changes) >= 4:
            acceleration = _build_acceleration_pattern(
                db=db,
                person_id=person_id,
                subject_type=subject_type,
                subject_id=group_subject_id,
                changes=comparable_changes,
                now=now,
            )
            if acceleration:
                if changes_truncated:
                    _append_limit_note(
                        db,
                        acceleration,
                        "Change assessment history was bounded to the most recent "
                        f"{MAX_PATTERN_CHANGE_ASSESSMENTS} records; earlier assessments were not included.",
                    )
                patterns.append(acceleration)
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

    # Reconcile lifecycle only when both source-event and change histories were
    # fully read. A bounded/truncated scan cannot prove that an older pattern is
    # unsupported, so it must not retire existing assessments.
    if not events_truncated and not changes_truncated:
        current_pattern_ids = {pattern.id for pattern in patterns}
        stale_query = db.query(Pattern).filter(
            Pattern.person_id == person_id,
            Pattern.subject_type == subject_type,
            Pattern.is_active.is_(True),
        )
        if subject_id is not None:
            stale_query = stale_query.filter(Pattern.subject_id == (subject_id or ""))
        stale_patterns = stale_query.order_by(Pattern.created_at.asc(), Pattern.id.asc()).all()
        for stale in stale_patterns:
            if stale.id in current_pattern_ids:
                continue
            stale.is_active = False
            alternatives = list(stale.alternative_interpretations or [])
            note = (
                "This assessment is no longer supported by the latest eligible source "
                "evidence during reconciliation. It is retained as historical context; "
                "the absence of a current pattern is not proof that the real-world event stopped."
            )
            if note not in alternatives:
                alternatives.append(note)
            stale.alternative_interpretations = alternatives
        if stale_patterns:
            db.flush()
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
            and candidate.pattern_metrics == pattern.pattern_metrics
            and candidate.alternative_interpretations == pattern.alternative_interpretations
            and candidate.frequency == pattern.frequency
            and candidate.rate_per_period == pattern.rate_per_period
            and candidate.pattern_strength == pattern.pattern_strength
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



def _build_frequency_pattern(
    db: Session,
    person_id: str,
    subject_type: str,
    subject_id: str,
    events: List[CareEvent],
    changes: List[Change],
    now: datetime,
) -> Optional[Pattern]:
    """Compare adjacent 30-day event rates only with complete observation coverage."""
    current_start = now - timedelta(days=30)
    prior_start = now - timedelta(days=60)
    prior_coverage = assess_observation_coverage(
        db, person_id, subject_type, prior_start, current_start
    )
    current_coverage = assess_observation_coverage(
        db, person_id, subject_type, current_start, now
    )
    if prior_coverage["coverage_status"] != "covered" or current_coverage["coverage_status"] != "covered":
        return None

    prior_events = [
        event for event in events
        if prior_start <= _ensure_utc(event.occurred_at) < current_start
    ]
    current_events = [
        event for event in events
        if current_start <= _ensure_utc(event.occurred_at) <= now
    ]
    prior_count = len(prior_events)
    current_count = len(current_events)
    if prior_count == current_count:
        return None

    increase = current_count > prior_count
    event_ids = sorted({event.id for event in prior_events + current_events})
    if not event_ids:
        return None
    metrics = {
        "comparison": "adjacent_equal_30_day_windows",
        "prior_window_start": prior_start.isoformat(),
        "prior_window_end": current_start.isoformat(),
        "current_window_start": current_start.isoformat(),
        "current_window_end": now.isoformat(),
        "prior_event_count": prior_count,
        "current_event_count": current_count,
        "prior_events_per_30_days": float(prior_count),
        "current_events_per_30_days": float(current_count),
        "absolute_rate_delta_per_30_days": float(current_count - prior_count),
        "prior_coverage_status": prior_coverage["coverage_status"],
        "current_coverage_status": current_coverage["coverage_status"],
        "prior_coverage_fraction": prior_coverage["coverage_fraction"],
        "current_coverage_fraction": current_coverage["coverage_fraction"],
    }
    return _persist_pattern(db, Pattern(
        person_id=person_id,
        subject_type=subject_type,
        subject_id=subject_id,
        pattern_type=PatternType.FREQUENCY_INCREASE if increase else PatternType.FREQUENCY_DECREASE,
        pattern_strength=PatternStrength.EMERGING if len(event_ids) >= 3 else PatternStrength.WEAK,
        event_ids=event_ids,
        change_ids=sorted({
            change.id for change in changes
            if (change.subject_id or "") == subject_id
            and change.is_active
            and set(change.evidence_ids or []).intersection(event_ids)
        }),
        temporal_start=prior_start,
        temporal_end=now,
        frequency=float(current_count),
        rate_per_period=float(current_count),
        direction=ChangeDirection.INCREASING if increase else ChangeDirection.DECREASING,
        confidence=None,
        evidence_count=len(event_ids),
        alternative_interpretations=[
            "Recorded-event frequency changed between adjacent 30-day windows with explicit complete coverage in both windows. This is descriptive and does not establish cause, clinical significance, or a forecast."
        ],
        pattern_metrics=metrics,
    ))


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
    # Rates require coverage over the entire analysis window, including time
    # before the first event and after the last event. Coverage only between
    # event timestamps cannot establish a valid denominator.
    # The observation denominator must cover every event included in the
    # recurrence, not merely the most recent 90 days. Keep a 90-day minimum
    # window for recent-only histories, but extend it to the earliest included
    # event when the pattern spans further back.
    coverage_window_start = min(now - timedelta(days=90), distinct_times[0])
    coverage_window_days = (now - coverage_window_start).total_seconds() / 86400.0
    coverage = assess_observation_coverage(
        db, person_id, subject_type, coverage_window_start, now
    )
    coverage_complete = coverage["coverage_status"] == "covered"
    rate_supported = coverage_complete and not history_truncated
    # Event recurrence is supported by multiple distinct source events, but a
    # rate requires an explicit observation denominator across the comparison span.
    interval_days = [
        (right - left).total_seconds() / 86400.0
        for left, right in zip(distinct_times, distinct_times[1:])
    ]
    frequency_per_day = (
        len(ordered_events) / coverage_window_days
        if rate_supported and coverage_window_days > 0 else None
    )
    rate_per_30_days = (
        len(ordered_events) / (coverage_window_days / 30.0)
        if rate_supported and coverage_window_days > 0 else None
    )
    interval_mean = mean(interval_days) if interval_days else None
    interval_std_dev = pstdev(interval_days) if len(interval_days) >= 2 else None
    interval_cv = (
        interval_std_dev / interval_mean
        if interval_std_dev is not None and interval_mean and interval_mean > 0
        else None
    )
    # Local-time distribution is computed only when every source event carries
    # a valid IANA timezone. UTC clock time is not a substitute for local routine
    # time, and a distribution alone does not establish a causal association.
    local_time_distribution = None
    local_time_distribution_status = "withheld_timezone_missing_or_invalid"
    try:
        local_datetimes = [
            _ensure_utc(event.occurred_at).astimezone(ZoneInfo(event.occurred_at_timezone))
            for event in ordered_events
            if event.occurred_at_timezone
        ]
        if len(local_datetimes) == len(ordered_events) and local_datetimes:
            local_time_distribution = {
                "hour_of_day_counts": {
                    str(hour): count for hour, count in sorted(Counter(value.hour for value in local_datetimes).items())
                },
                "day_of_week_counts": {
                    str(day): count for day, count in sorted(Counter(value.weekday() for value in local_datetimes).items())
                },
                "timezone_ids": sorted({event.occurred_at_timezone for event in ordered_events}),
            }
            local_time_distribution_status = "descriptive_only_no_causal_association"
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        local_time_distribution = None
        local_time_distribution_status = "withheld_timezone_missing_or_invalid"
    pattern_metrics = {
        "metric_version": "inter-event-intervals-v1",
        "local_time_distribution": local_time_distribution,
        "local_time_distribution_status": local_time_distribution_status,
        "distinct_observation_times": len(distinct_times),
        "distinct_event_records": len(ordered_events),
        "span_days": round(span_days, 6),
        "analysis_window_start": coverage_window_start.isoformat(),
        "analysis_window_end": now.isoformat(),
        "analysis_window_days": round(coverage_window_days, 6),
        "inter_event_interval_count": len(interval_days),
        "inter_event_intervals_days": [round(value, 6) for value in interval_days],
        "minimum_interval_days": round(min(interval_days), 6) if interval_days else None,
        "median_interval_days": round(median(interval_days), 6) if interval_days else None,
        "maximum_interval_days": round(max(interval_days), 6) if interval_days else None,
        "mean_interval_days": round(interval_mean, 6) if interval_mean is not None else None,
        "interval_population_std_dev_days": round(interval_std_dev, 6) if interval_std_dev is not None else None,
        "interval_coefficient_of_variation": round(interval_cv, 6) if interval_cv is not None else None,
        "regularity_interpretation": (
            "Descriptive interval variability only; no calibrated regularity threshold or forecast is applied."
            if len(interval_days) >= 3
            else "Too few inter-event intervals for even a descriptive variability estimate."
        ),
        "observation_coverage_status": coverage["coverage_status"],
        "normalized_rate_available": rate_supported,
        "source_history_truncated": history_truncated,
    }
    alternatives = []
    if not coverage_complete:
        alternatives.append(
            "Recurrence is based on distinct recorded events. A normalized frequency/rate is withheld because observation coverage across the event span is not explicitly complete."
        )
    if coverage["limitations"]:
        alternatives.extend(coverage["limitations"])
    if history_truncated:
        alternatives.append(
            f"Source event history was bounded to the most recent {MAX_PATTERN_SOURCE_EVENTS} records; the normalized rate is withheld because the event denominator may be incomplete."
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
        pattern_metrics=pattern_metrics,
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
        and change.baseline_coverage_status == "covered"
        and change.current_coverage_status == "covered"
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
    valid_event_ids = set(event_ids)
    ordered = [
        change for change in ordered
        if valid_event_ids.intersection(change.evidence_ids or [])
    ]
    if len(ordered) < 3:
        return None
    event_ids = sorted({
        evidence_id
        for change in ordered
        for evidence_id in (change.evidence_ids or [])
        if evidence_id in valid_event_ids
    })
    if len(event_ids) < 3:
        return None

    change_ids = [change.id for change in ordered]
    temporal_start = min(_ensure_utc(change.comparison_window_start) for change in ordered)
    temporal_end = max(_ensure_utc(change.comparison_window_end) for change in ordered)
    # Persisted status fields are provenance snapshots, not substitutes for the
    # coverage ledger. Re-check the entire trend span before classifying it.
    coverage = assess_observation_coverage(
        db, person_id, subject_type, temporal_start, temporal_end
    )
    if coverage["coverage_status"] != "covered":
        return None
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


def _build_acceleration_pattern(
    db: Session,
    person_id: str,
    subject_type: str,
    subject_id: str,
    changes: List[Change],
    now: datetime,
) -> Optional[Pattern]:
    """Detect increasing change magnitude only from covered, source-linked windows.

    This is a descriptive pattern, not a calibrated forecast. At least four
    distinct assessments are required so acceleration is based on three
    successive magnitude differences rather than a two-point change.
    """
    comparable = [
        change for change in changes
        if change.magnitude is not None
        and change.comparison_window_start is not None
        and change.comparison_window_end is not None
        and change.change_direction in (ChangeDirection.INCREASING, ChangeDirection.DECREASING)
        and change.baseline_coverage_status == "covered"
        and change.current_coverage_status == "covered"
    ]
    ordered = sorted(
        comparable,
        key=lambda change: (_ensure_utc(change.comparison_window_end), change.id),
    )
    # Keep one assessment per comparison end to avoid duplicate analysis runs
    # being counted as separate longitudinal observations.
    unique_ordered = []
    seen_ends = set()
    for change in ordered:
        end = _ensure_utc(change.comparison_window_end)
        if end not in seen_ends:
            seen_ends.add(end)
            unique_ordered.append(change)
    if len(unique_ordered) < 4:
        return None
    directions = {change.change_direction for change in unique_ordered}
    if len(directions) != 1:
        return None
    magnitudes = [float(change.magnitude) for change in unique_ordered]
    successive_deltas = [
        magnitudes[index + 1] - magnitudes[index]
        for index in range(len(magnitudes) - 1)
    ]
    # A monotonic trend alone is not acceleration. The successive change
    # magnitudes themselves must increase across at least three intervals.
    if not all(delta > 0 for delta in successive_deltas) or not all(
        successive_deltas[index + 1] > successive_deltas[index]
        for index in range(len(successive_deltas) - 1)
    ):
        return None
    temporal_start = min(_ensure_utc(change.comparison_window_start) for change in unique_ordered)
    temporal_end = max(_ensure_utc(change.comparison_window_end) for change in unique_ordered)
    coverage = assess_observation_coverage(
        db, person_id, subject_type, temporal_start, temporal_end
    )
    if coverage["coverage_status"] != "covered":
        return None
    event_ids = _validated_source_event_ids(
        db=db,
        person_id=person_id,
        subject_type=subject_type,
        candidate_ids=[
            evidence_id
            for change in unique_ordered
            for evidence_id in (change.evidence_ids or [])
        ],
        reference_time=_ensure_utc(now),
    )
    if len(event_ids) < 4:
        return None
    valid_event_ids = set(event_ids)
    unique_ordered = [
        change for change in unique_ordered
        if valid_event_ids.intersection(change.evidence_ids or [])
    ]
    if len(unique_ordered) < 4:
        return None
    event_ids = sorted({
        evidence_id
        for change in unique_ordered
        for evidence_id in (change.evidence_ids or [])
        if evidence_id in valid_event_ids
    })
    if len(event_ids) < 4:
        return None
    pattern = Pattern(
        person_id=person_id,
        subject_type=subject_type,
        subject_id=subject_id,
        pattern_type=PatternType.ACCELERATION,
        pattern_strength=PatternStrength.EMERGING,
        event_ids=event_ids,
        change_ids=[change.id for change in unique_ordered],
        temporal_start=temporal_start,
        temporal_end=temporal_end,
        direction=next(iter(directions)),
        confidence=None,
        evidence_count=len(event_ids),
        alternative_interpretations=[
            "Recorded change magnitudes increased across successive covered windows; this is descriptive, not a calibrated forecast or evidence of cause."
        ],
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
        and change.baseline_coverage_status == "covered"
        and change.current_coverage_status == "covered"
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
    valid_event_ids = set(event_ids)
    ordered = [
        change for change in ordered
        if valid_event_ids.intersection(change.evidence_ids or [])
    ]
    if len(ordered) < 2:
        return None
    event_ids = sorted({
        evidence_id
        for change in ordered
        for evidence_id in (change.evidence_ids or [])
        if evidence_id in valid_event_ids
    })
    if len(event_ids) < 2:
        return None
    change_ids = [change.id for change in ordered]
    temporal_start = min(_ensure_utc(change.comparison_window_start) for change in ordered)
    temporal_end = max(_ensure_utc(change.comparison_window_end) for change in ordered)
    coverage = assess_observation_coverage(
        db, person_id, subject_type, temporal_start, temporal_end
    )
    if coverage["coverage_status"] != "covered":
        return None
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



def _build_clustering_pattern(
    db: Session,
    person_id: str,
    subject_type: str,
    subject_id: str,
    events: List[CareEvent],
) -> Optional[Pattern]:
    """Find a bounded burst of distinct events without inferring cause or rate."""
    ordered = sorted(
        events,
        key=lambda event: (_ensure_utc(event.occurred_at), event.id),
    )
    best_cluster: List[CareEvent] = []
    left = 0
    for right, event in enumerate(ordered):
        event_time = _ensure_utc(event.occurred_at)
        while left <= right and (
            event_time - _ensure_utc(ordered[left].occurred_at)
        ) > timedelta(days=7):
            left += 1
        candidate = ordered[left:right + 1]
        distinct_times = {_ensure_utc(item.occurred_at) for item in candidate}
        if len(candidate) >= 3 and len(distinct_times) >= 3 and len(candidate) > len(best_cluster):
            best_cluster = candidate
    if len(best_cluster) < 3:
        return None

    start = _ensure_utc(best_cluster[0].occurred_at)
    end = _ensure_utc(best_cluster[-1].occurred_at)
    event_ids = [event.id for event in best_cluster]
    alternatives = [
        "A cluster means distinct recorded events occurred within a seven-day window; it does not establish a cause, severity trend, or clinical relationship."
    ]
    pattern = Pattern(
        person_id=person_id,
        subject_type=subject_type,
        subject_id=subject_id,
        pattern_type=PatternType.CLUSTERING,
        pattern_strength=PatternStrength.ESTABLISHED if len(best_cluster) >= 4 else PatternStrength.EMERGING,
        event_ids=event_ids,
        change_ids=[],
        temporal_start=start,
        temporal_end=end,
        direction=ChangeDirection.UNKNOWN,
        confidence=None,
        evidence_count=len(event_ids),
        alternative_interpretations=alternatives,
    )
    return _persist_pattern(db, pattern)


def detect_cross_domain_patterns(
    db: Session,
    person_id: str,
    reference_time: Optional[datetime] = None,
    cluster_days: int = 7,
) -> List[Pattern]:
    """Persist co-occurring recorded events across domains without inferring causation."""
    now = _ensure_utc(reference_time or _utc_now())
    window_start = now - timedelta(days=90)
    queried = (
        db.query(CareEvent)
        .filter(
            CareEvent.person_id == person_id,
            CareEvent.status.notin_(("invalidated", "superseded", "unparsed_raw")),
            CareEvent.occurred_at >= window_start,
            CareEvent.occurred_at <= now,
        )
        .order_by(CareEvent.occurred_at.desc(), CareEvent.id.asc())
        .limit(MAX_PATTERN_SOURCE_EVENTS + 1)
        .all()
    )
    truncated = len(queried) > MAX_PATTERN_SOURCE_EVENTS
    events = queried[:MAX_PATTERN_SOURCE_EVENTS]
    events.sort(key=lambda event: (_ensure_utc(event.occurred_at), event.id))

    # Deduplicate only records tied to the same source evidence and occurrence
    # timestamp. Similar descriptions alone do not establish duplicate identity.
    canonical = []
    seen = set()
    for event in events:
        signature = tuple(sorted(set(event.evidence_ids or [])))
        identity = (
            ("source", signature, _ensure_utc(event.occurred_at))
            if signature else ("event", event.id)
        )
        if identity in seen:
            continue
        seen.add(identity)
        canonical.append(event)

    best_cluster = []
    left = 0
    for right, event in enumerate(canonical):
        event_time = _ensure_utc(event.occurred_at)
        while left <= right and (
            event_time - _ensure_utc(canonical[left].occurred_at)
        ).total_seconds() > cluster_days * 86400:
            left += 1
        candidate = canonical[left:right + 1]
        domains = {item.event_type for item in candidate if item.event_type}
        if len(domains) >= 2 and len(candidate) >= 3:
            if (
                len(candidate) > len(best_cluster)
                or (
                    len(candidate) == len(best_cluster)
                    and _ensure_utc(candidate[-1].occurred_at) > _ensure_utc(best_cluster[-1].occurred_at)
                )
            ):
                best_cluster = candidate

    current_pattern = None
    if best_cluster:
        event_ids = sorted({event.id for event in best_cluster})
        domain_counts = dict(sorted(Counter(event.event_type for event in best_cluster).items()))
        linked_changes = (
            db.query(Change)
            .filter(
                Change.person_id == person_id,
                Change.is_active.is_(True),
            )
            .order_by(Change.created_at.asc(), Change.id.asc())
            .all()
        )
        linked_change_ids = sorted({
            change.id for change in linked_changes
            if set(change.evidence_ids or []).intersection(event_ids)
        })
        cluster_start = min(_ensure_utc(event.occurred_at) for event in best_cluster)
        cluster_end = max(_ensure_utc(event.occurred_at) for event in best_cluster)
        current_pattern = _persist_pattern(db, Pattern(
            person_id=person_id,
            subject_type="cross_domain",
            subject_id="",
            pattern_type=PatternType.CONVERGENCE,
            pattern_strength=PatternStrength.EMERGING,
            event_ids=event_ids,
            change_ids=linked_change_ids,
            temporal_start=cluster_start,
            temporal_end=cluster_end,
            frequency=None,
            rate_per_period=None,
            direction=ChangeDirection.UNKNOWN,
            confidence=None,
            evidence_count=len(event_ids),
            alternative_interpretations=[
                "Distinct recorded events from multiple care domains occurred within the configured time window. This is co-occurrence only; it does not establish a shared cause, clinical relationship, or forecast."
            ],
            pattern_metrics={
                "metric_version": "cross-domain-co-occurrence-v1",
                "window_days": cluster_days,
                "analysis_window_start": window_start.isoformat(),
                "analysis_window_end": now.isoformat(),
                "cluster_start": cluster_start.isoformat(),
                "cluster_end": cluster_end.isoformat(),
                "event_count": len(event_ids),
                "domain_counts": domain_counts,
                "distinct_domain_count": len(domain_counts),
                "source_history_truncated": truncated,
                "normalized_rate_available": False,
            },
        ))

    # A bounded read cannot prove that a prior cluster is unsupported. Only
    # retire prior cross-domain assessments after a complete scan.
    if not truncated:
        stale = (
            db.query(Pattern)
            .filter(
                Pattern.person_id == person_id,
                Pattern.subject_type == "cross_domain",
                Pattern.subject_id == "",
                Pattern.pattern_type == PatternType.CONVERGENCE,
                Pattern.is_active.is_(True),
            )
            .order_by(Pattern.created_at.asc(), Pattern.id.asc())
            .all()
        )
        for pattern in stale:
            if current_pattern is not None and pattern.id == current_pattern.id:
                continue
            pattern.is_active = False
            note = (
                "The latest complete source-event scan no longer supports this recorded co-occurrence window. "
                "This does not prove that any real-world event stopped or that the domains are unrelated."
            )
            alternatives = list(pattern.alternative_interpretations or [])
            if note not in alternatives:
                alternatives.append(note)
            pattern.alternative_interpretations = alternatives
        if stale:
            db.flush()

    return [current_pattern] if current_pattern is not None else []
