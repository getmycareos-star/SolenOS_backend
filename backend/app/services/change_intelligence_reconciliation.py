"""Shared synchronous reconciliation for persisted change and pattern intelligence.

Call after source writes so late-arriving events and corrections do not rely on a
caregiver reopening the change-intelligence screen to refresh derived assessments.
This service does not commit: callers own the transaction boundary.
"""
from datetime import datetime
import logging
from typing import Optional

from sqlalchemy.orm import Session

from app.models.care import CareEvent
from app.services.change_detection import (
    detect_assertion_changes,
    detect_event_changes,
    detect_numeric_observation_changes,
)
from app.services.pattern_detection import detect_cross_domain_patterns, detect_patterns
from app.services.care_continuity import next_best_questions
from app.services.proactive_care_communications import create_attention_change_notifications

logger = logging.getLogger(__name__)


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
    # Keep source-linked clarification questions in step with every ordinary
    # evidence/assertion reconciliation. The caller retains transaction ownership.
    next_best_questions(db, person_id, persist=True, commit=False)
    db.flush()
    # In-app communication is downstream of the evidence/significance pipeline.
    # A notification failure must not roll back a successfully saved care update.
    try:
        with db.begin_nested():
            create_attention_change_notifications(db, person_id, reference_time)
    except Exception:
        logger.exception("Could not create in-app notifications for care changes on person %s", person_id)
