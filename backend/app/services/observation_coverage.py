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
    intervals = sorted(
        (max(start, _utc(r.window_start)), min(end, _utc(r.window_end)))
        for r in rows if r.coverage_status == "covered"
    )
    covered_seconds = 0
    cursor = start
    for left, right in intervals:
        if right <= cursor:
            continue
        left = max(left, cursor)
        if right > left:
            covered_seconds += int((right - left).total_seconds())
            cursor = right
    window_seconds = int((end - start).total_seconds())
    has_explicit_interval_status = any(r.coverage_status in {"covered", "uncovered"} for r in rows)
    # No coverage rows means unknown, not a measured 0% observation rate.
    fraction = covered_seconds / window_seconds if window_seconds > 0 and has_explicit_interval_status else None
    has_noncovered = any(r.coverage_status != "covered" for r in rows)
    if fraction == 1.0 and not has_noncovered:
        status = "covered"
        limitations: List[str] = []
    elif covered_seconds > 0 or any(r.coverage_status == "partial" for r in rows):
        status = "partial"
        limitations = ["Observation coverage is incomplete; event absence cannot be inferred for uncovered intervals."]
    elif rows and all(r.coverage_status == "uncovered" for r in rows):
        status = "uncovered"
        limitations = ["The recorded interval was explicitly marked unobserved; no negative event claim is supported."]
    else:
        status = "unknown"
        limitations = ["No complete observation-coverage record establishes whether this domain was observed."]
    return {
        "person_id": person_id, "subject_type": subject_type,
        "window_start": start, "window_end": end, "coverage_status": status,
        "covered_seconds": covered_seconds, "window_seconds": window_seconds,
        "coverage_fraction": fraction, "records": rows, "limitations": limitations,
    }
