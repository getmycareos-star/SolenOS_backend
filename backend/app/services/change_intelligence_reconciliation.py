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
from app.services.care_continuity import (
    next_best_questions,
    reassess_open_question_lifecycle,
    reconcile_information_gaps_from_accepted_assertions,
)
from app.services.proactive_care_communications import create_meaningful_change_notifications

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
    # A reviewed, evidence-backed current-state assertion can settle the
    # existence of an exact matching information gap. Conflicts and unreviewed
    # reports remain unresolved; no assertion is upgraded to verified.
    reconcile_information_gaps_from_accepted_assertions(db, person_id)
    # Keep source-linked clarification questions in step with every ordinary
    # evidence/assertion reconciliation. The caller retains transaction ownership.
    next_best_questions(db, person_id, persist=True, commit=False)
    # Reassess previously necessary, source-linked requests after new evidence
    # has been reconciled. Automatic close/reopen transitions are captured by
    # the existing append-only care-state revision history.
    reassess_open_question_lifecycle(db, person_id)
    db.flush()
    # Only notify after evidence reconciliation and significance assessment.
    # A notification failure must not undo the saved care update.
    try:
        with db.begin_nested():
            create_meaningful_change_notifications(db, person_id, now=reference_time)
    except Exception:
        logger.exception("Care-change notification creation failed for person %s", person_id)
