"""Shared synchronous reconciliation for persisted change and pattern intelligence.

Call after source writes so late-arriving events and corrections do not rely on a
caregiver reopening the change-intelligence screen to refresh derived assessments.
This service does not commit: callers own the transaction boundary.
"""
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from app.models.care import CareEvent
from app.services.change_detection import (
    detect_assertion_changes,
    detect_event_changes,
    detect_numeric_observation_changes,
)
from app.services.pattern_detection import detect_cross_domain_patterns, detect_patterns


def reconcile_person_change_intelligence(
    db: Session,
    person_id: str,
    reference_time: Optional[datetime] = None,
) -> None:
    """Recompute all supported assessments for one person's persisted care record."""
    event_types = [
        row[0]
        for row in (
            db.query(CareEvent.event_type)
            .filter(
                CareEvent.person_id == person_id,
                CareEvent.event_type.is_not(None),
            )
            .distinct()
            .order_by(CareEvent.event_type.asc())
            .all()
        )
        if row[0]
    ]
    for event_type in event_types:
        detect_event_changes(db, person_id, event_type, None, reference_time)
        # Run patterns after change assessments have been flushed in this transaction.
        detect_patterns(db, person_id, event_type, None, reference_time)
    detect_cross_domain_patterns(db, person_id, reference_time)
    detect_assertion_changes(db, person_id)
    detect_numeric_observation_changes(db, person_id)
    db.flush()
