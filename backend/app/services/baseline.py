from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple
from sqlalchemy.orm import Session
from app.models.care import CareEvent
from app.models.change import Baseline
from app.core.change_enums import BaselineType, BaselineConfidence
from app.services.temporal_intelligence import utc_now
from app.services.observation_coverage import assess_observation_coverage


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def select_baseline_window(
    reference_time: datetime,
    candidate_windows: List[Tuple[datetime, datetime]],
    min_days: int = 30,
    max_gap_days: int = 60,
) -> Optional[Tuple[datetime, datetime]]:
    candidate_windows = sorted(candidate_windows, key=lambda w: w[0])
    best_window = None
    best_score = -1
    for window_start, window_end in candidate_windows:
        duration = (window_end - window_start).days
        gap = (reference_time - window_end).days
        if duration < min_days:
            continue
        if gap > max_gap_days:
            continue
        score = duration - gap * 0.5
        if score > best_score:
            best_score = score
            best_window = (window_start, window_end)
    return best_window


def get_longitudinal_event_counts(
    db: Session,
    person_id: str,
    event_type: str,
    subject_id: Optional[str],
    now: datetime,
) -> dict:
    now = _ensure_utc(now)
    query = db.query(CareEvent).filter(
        CareEvent.person_id == person_id,
        CareEvent.event_type == event_type,
        CareEvent.status.notin_(("invalidated", "superseded", "unparsed_raw")),
    )
    if subject_id:
        query = query.filter(CareEvent.id == subject_id)
    queried_events = query.order_by(CareEvent.occurred_at.asc(), CareEvent.id.asc()).all()
    # Exact source-evidence reuse is a duplicate-record signal. Do not deduplicate
    # by title or nearby timestamps alone; those can represent distinct events.
    events = []
    seen_source_signatures = set()
    for event in queried_events:
        evidence_signature = tuple(sorted(set(event.evidence_ids or [])))
        identity = ("evidence", evidence_signature) if evidence_signature else ("event", event.id)
        if identity in seen_source_signatures:
            continue
        seen_source_signatures.add(identity)
        events.append(event)

    six_weeks_ago = now - timedelta(days=42)
    current_window_count = sum(1 for e in events if _ensure_utc(e.occurred_at) >= six_weeks_ago)
    historical_count = sum(1 for e in events if _ensure_utc(e.occurred_at) < six_weeks_ago)
    recent_events = [e for e in events if _ensure_utc(e.occurred_at) >= six_weeks_ago]
    return {
        "total_events": len(events),
        "current_window_count": current_window_count,
        "historical_count": historical_count,
        "current_window_start": six_weeks_ago,
        "current_window_end": now,
        "recent_events": recent_events,
        "all_events": events,
    }


def build_baseline(
    db: Session,
    person_id: str,
    event_type: str,
    subject_id: Optional[str],
    reference_time: Optional[datetime] = None,
) -> Optional[Baseline]:
    now = _ensure_utc(reference_time) or _ensure_utc(utc_now())
    counts = get_longitudinal_event_counts(db, person_id, event_type, subject_id, now)
    events = counts["all_events"]
    if not events:
        return None
    baseline_window_end = now - timedelta(days=42)
    baseline_events = [
        event for event in events
        if _ensure_utc(event.occurred_at) < baseline_window_end
    ]
    if baseline_events:
        # Use the full available historical span, ending before the comparison
        # window. This preserves older longitudinal evidence without pretending
        # that events outside an arbitrary lookback never happened.
        window_start = min(_ensure_utc(event.occurred_at) for event in baseline_events)
        window_end = baseline_window_end
    else:
        window_start = now - timedelta(days=180)
        window_end = baseline_window_end
    event_count = len(baseline_events)
    window_days = max((_ensure_utc(window_end) - _ensure_utc(window_start)).days, 1)
    coverage = assess_observation_coverage(db, person_id, event_type, window_start, window_end)
    coverage_complete = coverage["coverage_status"] == "covered"
    # Do not expose a normalized occurrence rate without an explicit observation
    # denominator. Counts remain available as recorded evidence, not as true rates.
    frequency_per_period = (
        event_count / (window_days / 30.0)
        if coverage_complete and event_count
        else 0.0 if coverage_complete else None
    )
    # Event volume alone cannot establish a reliable baseline when the domain
    # may not have been observed. Coverage gates stability and confidence.
    confidence = (
        BaselineConfidence.LOW
        if not coverage_complete
        else BaselineConfidence.HIGH
        if event_count >= 5
        else BaselineConfidence.MODERATE
        if event_count >= 2
        else BaselineConfidence.LOW
    )
    observation_density = coverage["coverage_fraction"]
    evidence_ids = sorted({e.id for e in baseline_events if e.id})
    existing_baselines = (
        db.query(Baseline)
        .filter(
            Baseline.person_id == person_id,
            Baseline.subject_type == event_type,
            Baseline.subject_id == (subject_id or ""),
            Baseline.baseline_type == BaselineType.PERSONAL_HISTORICAL,
            Baseline.is_active.is_(True),
        )
        .order_by(Baseline.created_at.desc())
        .all()
    )
    for existing in existing_baselines:
        same_source_events = (
            set(existing.evidence_ids or []) == set(evidence_ids)
            if evidence_ids
            else (
                not (existing.evidence_ids or [])
                and _ensure_utc(existing.window_start).date() == _ensure_utc(window_start).date()
                and _ensure_utc(existing.window_end).date() == _ensure_utc(window_end).date()
            )
        )
        same_density = (
            existing.observation_density is None and observation_density is None
        ) or (
            existing.observation_density is not None
            and observation_density is not None
            and abs(existing.observation_density - observation_density) < 1e-9
        )
        if (
            same_source_events
            and existing.event_count == event_count
            and _ensure_utc(existing.window_start) == _ensure_utc(window_start)
            and _ensure_utc(existing.window_end) == _ensure_utc(window_end)
            and existing.confidence == confidence
            and bool(existing.is_stable) == coverage_complete
            and same_density
            and bool(existing.documentation_bias_flag) == (not coverage_complete)
        ):
            return existing

    # A coverage update or changed source history changes baseline provenance.
    # Preserve the prior baseline as history and keep one active baseline only.
    for existing in existing_baselines:
        existing.is_active = False
    if existing_baselines:
        db.flush()

    baseline = Baseline(
        person_id=person_id,
        subject_type=event_type,
        subject_id=subject_id or "",
        baseline_type=BaselineType.PERSONAL_HISTORICAL,
        window_start=window_start,
        window_end=window_end,
        event_count=event_count,
        frequency_per_period=frequency_per_period,
        state_value=None,
        confidence=confidence,
        is_stable=coverage_complete,
        observation_density=observation_density,
        documentation_bias_flag=not coverage_complete,
        evidence_ids=evidence_ids,
    )
    db.add(baseline)
    db.flush()
    for existing in existing_baselines:
        existing.superseded_by_baseline_id = baseline.id
    if existing_baselines:
        db.flush()
    return baseline


def compare_to_baseline(
    baseline: Baseline,
    current_count: int,
    current_window_days: int = 42,
) -> dict:
    if baseline.frequency_per_period is None:
        frequency_delta = None
        relative_change = None
    else:
        current_frequency = current_count / (current_window_days / 30.0) if current_window_days > 0 else 0.0
        frequency_delta = current_frequency - baseline.frequency_per_period
        if baseline.frequency_per_period == 0:
            relative_change = float('inf') if current_frequency > 0 else 0.0
        else:
            relative_change = (
                (frequency_delta / baseline.frequency_per_period) * 100
                if baseline.frequency_per_period > 0
                else None
            )
    return {
        "baseline_event_count": baseline.event_count,
        "baseline_window_days": max((_ensure_utc(baseline.window_end) - _ensure_utc(baseline.window_start)).days, 1),
        "baseline_frequency_per_period": baseline.frequency_per_period,
        "current_count": current_count,
        "current_window_days": current_window_days,
        "frequency_delta": frequency_delta,
        "relative_change_percent": relative_change,
        "baseline_confidence": baseline.confidence.value,
        "baseline_is_stable": baseline.is_stable,
        "observation_density": baseline.observation_density,
    }
