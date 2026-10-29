"""Coverage-aware comparison primitives; absence of records is not negative evidence."""
from datetime import datetime, timezone
from typing import Any, Dict, List
from sqlalchemy.orm import Session
from app.models.observation_coverage import ObservationCoverage


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def assess_observation_coverage(
    db: Session, person_id: str, subject_type: str,
    window_start: datetime, window_end: datetime,
) -> Dict[str, Any]:
    start, end = _utc(window_start), _utc(window_end)
    if end <= start:
        raise ValueError("window_end must be later than window_start")
    rows = (
        db.query(ObservationCoverage)
        .filter(
            ObservationCoverage.person_id == person_id,
            ObservationCoverage.subject_type == subject_type,
            ObservationCoverage.window_start < end,
            ObservationCoverage.window_end > start,
        )
        .order_by(ObservationCoverage.window_start.asc(), ObservationCoverage.id.asc())
        .all()
    )
    intervals = [
        (max(start, _utc(r.window_start)), min(end, _utc(r.window_end)), r.coverage_status)
        for r in rows
    ]
    boundaries = {start, end}
    for left, right, _status in intervals:
        boundaries.add(left)
        boundaries.add(right)

    ordered_boundaries = sorted(boundaries)
    covered_seconds = 0
    uncovered_seconds = 0
    unknown_gap_seconds = 0
    conflicting_seconds = 0
    for left, right in zip(ordered_boundaries, ordered_boundaries[1:]):
        if right <= left:
            continue
        active_statuses = {
            status for interval_start, interval_end, status in intervals
            if interval_start <= left and interval_end >= right
        }
        seconds = int((right - left).total_seconds())
        if active_statuses == {"covered"}:
            covered_seconds += seconds
        elif active_statuses == {"uncovered"}:
            uncovered_seconds += seconds
        elif not active_statuses:
            unknown_gap_seconds += seconds
        else:
            # Partial, unknown, or conflicting overlapping declarations cannot
            # be upgraded to covered or uncovered without finer-grained data.
            conflicting_seconds += seconds

    window_seconds = int((end - start).total_seconds())
    has_explicit_interval_status = any(r.coverage_status in {"covered", "uncovered"} for r in rows)
    # No coverage rows means unknown, not a measured 0% observation rate.
    fraction = (
        covered_seconds / window_seconds
        if window_seconds > 0 and has_explicit_interval_status else None
    )
    if fraction == 1.0:
        status = "covered"
        limitations: List[str] = []
    elif covered_seconds > 0 or any(r.coverage_status == "partial" for r in rows):
        status = "partial"
        limitations = [
            "Observation coverage is incomplete or conflicting; event absence cannot be inferred for uncovered or unknown intervals."
        ]
    elif (
        rows
        and uncovered_seconds == window_seconds
        and unknown_gap_seconds == 0
        and conflicting_seconds == 0
    ):
        status = "uncovered"
        limitations = [
            "The entire requested interval was explicitly marked unobserved; no negative event claim is supported."
        ]
    else:
        status = "unknown"
        limitations = [
            "The available coverage records do not establish observation status for the entire requested interval."
        ]
    return {
        "person_id": person_id, "subject_type": subject_type,
        "window_start": start, "window_end": end, "coverage_status": status,
        "covered_seconds": covered_seconds, "window_seconds": window_seconds,
        "coverage_fraction": fraction, "records": rows, "limitations": limitations,
    }
