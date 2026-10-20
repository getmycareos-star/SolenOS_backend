"""Shared helpers for the deep-care services.

These mirror the conventions already used across app.services rather than
introducing a new one. Duplication of _utc_now and _ensure_utc already exists in
several modules in this codebase and adding an eleventh copy would be worse than
centralising, so new code uses these.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence

from sqlalchemy.orm import Session

from app.models.evidence import Evidence


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def parse_time(value: Any) -> Optional[datetime]:
    """Best-effort parse of anything that might be a timestamp.

    Routers pass ISO strings through unchanged in several places, so the parse
    has to be forgiving. Returns None rather than raising, because an unparsable
    time is a fact about the input, not an error to propagate.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return ensure_utc(value)
    text = str(value).strip()
    if not text:
        return None
    candidate = text.replace("Z", "+00:00")
    try:
        return ensure_utc(datetime.fromisoformat(candidate))
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y"):
        try:
            return ensure_utc(datetime.strptime(text, fmt))
        except ValueError:
            continue
    return None


def days_between(earlier: Optional[datetime], later: Optional[datetime]) -> Optional[int]:
    earlier = ensure_utc(earlier)
    later = ensure_utc(later)
    if earlier is None or later is None:
        return None
    return int((later - earlier).days)


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    if math.isnan(value) or math.isinf(value):
        return low
    return round(min(max(value, low), high), 3)


def unique(values: Iterable[Any]) -> List[str]:
    seen = set()
    result: List[str] = []
    for value in values:
        if value is None:
            continue
        item = str(value)
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def evidence_map(db: Session, evidence_ids: Sequence[str]) -> Dict[str, str]:
    """Fetch source text for a set of evidence ids, keyed by id.

    Callers pass the real evidence text to the model so it is reasoning over the
    record rather than over a description of the record, and so the trust guard
    can check that what came back refers to something that exists.
    """
    ids = [eid for eid in unique(evidence_ids) if eid]
    if not ids:
        return {}
    rows = db.query(Evidence).filter(Evidence.id.in_(ids)).all()
    return {row.id: (row.source_text or "") for row in rows}


def supersede_active(db: Session, model, person_id: str, subject: str, new_id: str, pointer: str) -> None:
    """Retire the previous active row and point it at its replacement.

    Nothing is ever deleted or overwritten in place. Trust rule 5: an earlier
    reading stays readable, so a caregiver can see that the assessment changed
    and when.
    """
    existing = (
        db.query(model)
        .filter(model.person_id == person_id, model.subject == subject, model.is_active.is_(True))
        .all()
    )
    for row in existing:
        row.is_active = False
        setattr(row, pointer, new_id)


def mean(values: Sequence[float]) -> Optional[float]:
    clean = [v for v in values if v is not None and not math.isnan(v) and not math.isinf(v)]
    if not clean:
        return None
    return sum(clean) / len(clean)


def stdev(values: Sequence[float]) -> Optional[float]:
    clean = [v for v in values if v is not None and not math.isnan(v) and not math.isinf(v)]
    if len(clean) < 2:
        return None
    average = sum(clean) / len(clean)
    variance = sum((v - average) ** 2 for v in clean) / (len(clean) - 1)
    return math.sqrt(variance)


def add_days(moment: datetime, days: float) -> datetime:
    return ensure_utc(moment) + timedelta(days=days)
