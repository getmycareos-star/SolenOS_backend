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
from app.models.care_continuity import CareAssertion
from app.services.change_detection import (
    detect_assertion_changes,
    detect_event_changes,
    detect_numeric_observation_changes,
)
from app.services.pattern_detection import detect_cross_domain_patterns, detect_patterns
from app.services.proactive_care_communications import create_proactive_care_notifications

logger = logging.getLogger(__name__)
from app.services.care_continuity import (
    next_best_questions,
    reassess_open_question_lifecycle,
    reconcile_information_gaps_from_accepted_assertions,
)


def reconcile_person_change_intelligence(
    db: Session,
    person_id: str,
    reference_time: Optional[datetime] = None,
    *,
    protected_assertion_ids: Optional[list[str]] = None,
) -> None:
    """Recompute assessments while preserving caller-designated source assertions."""
    protected_ids = list(dict.fromkeys(
        str(value) for value in (protected_assertion_ids or []) if value
    ))

    def assert_source_assertions_preserved(stage: str) -> None:
        if not protected_ids:
            return
        present = {
            str(row[0]) for row in db.query(CareAssertion.id).filter(
                CareAssertion.person_id == person_id,
                CareAssertion.id.in_(protected_ids),
            ).all()
        }
        missing = set(protected_ids) - present
        if missing:
            logger.error(
                "Care reconciliation removed protected source assertions at stage %s",
                stage,
            )
            raise RuntimeError(
                f"Source assertion preservation failed during {stage}"
            )

    assert_source_assertions_preserved("start")
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
        assert_source_assertions_preserved("event_change_detection")
        # Run patterns after change assessments have been flushed in this transaction.
        detect_patterns(db, person_id, event_type, None, reference_time)
    detect_cross_domain_patterns(db, person_id, reference_time)
    assert_source_assertions_preserved("cross_domain_patterns")
    detect_assertion_changes(db, person_id)
    assert_source_assertions_preserved("assertion_change_detection")
    detect_numeric_observation_changes(db, person_id)
    assert_source_assertions_preserved("numeric_observation_changes")
    # A reviewed, evidence-backed current-state assertion can settle the
    # existence of an exact matching information gap. Conflicts and unreviewed
    # reports remain unresolved; no assertion is upgraded to verified.
    reconcile_information_gaps_from_accepted_assertions(db, person_id)
    assert_source_assertions_preserved("information_gap_reconciliation")
    # Keep source-linked clarification questions in step with every ordinary
    # evidence/assertion reconciliation. The caller retains transaction ownership.
    next_best_questions(db, person_id, persist=True, commit=False)
    assert_source_assertions_preserved("question_reconciliation")
    # Reassess previously necessary, source-linked requests after new evidence
    # has been reconciled. Automatic close/reopen transitions are captured by
    # the existing append-only care-state revision history.
    reassess_open_question_lifecycle(db, person_id)
    assert_source_assertions_preserved("question_lifecycle")
    db.flush()
    # Communication is downstream of reconciliation. Keep notification writes
    # inside a savepoint so an inbox failure cannot undo the care-record update.
    try:
        with db.begin_nested():
            create_proactive_care_notifications(db, person_id, now=reference_time)
            assert_source_assertions_preserved("proactive_communications")
    except Exception:
        logger.exception("Proactive in-app care communication failed for person %s", person_id)
