"""Shared synchronous reconciliation for persisted change and pattern intelligence.

Call after source writes so late-arriving events and corrections do not rely on a
caregiver reopening the change-intelligence screen to refresh derived assessments.
This service does not commit: callers own the transaction boundary.
"""
from datetime import datetime, timezone
import logging
import uuid
from typing import Optional

from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.models.care import CareEvent, Caregiver, Evidence
from app.models.change import Change
from app.models.notifications import CareNotification
from app.models.uncertainty import OpenQuestion
from app.core.change_enums import SignificanceVerdict
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



logger = logging.getLogger(__name__)


def _stable_notification_id(person_id: str, caregiver_id: str, category: str, source_id: str, revision: str = "1") -> str:
    identity = "|".join(("solenos", person_id, caregiver_id, category, source_id, revision))
    return str(uuid.uuid5(uuid.NAMESPACE_URL, identity))


def _persist_inbox_item(
    db: Session,
    *,
    person_id: str,
    caregiver_id: str,
    category: str,
    source_id: str,
    title: str,
    body: str,
    created_at: datetime,
    revision: str = "1",
) -> bool:
    notification_id = _stable_notification_id(
        person_id, caregiver_id, category, source_id, revision
    )
    if db.query(CareNotification.id).filter(CareNotification.id == notification_id).first():
        return False
    try:
        # The deterministic primary key makes concurrent reconciliation idempotent
        # without requiring another table or a schema migration.
        with db.begin_nested():
            db.add(CareNotification(
                id=notification_id,
                person_id=person_id,
                caregiver_id=caregiver_id,
                title=title,
                body=body,
                category=category,
                priority="normal",
                source_event_id=source_id,
                created_at=created_at,
            ))
            db.flush()
    except IntegrityError:
        return False
    return True


def create_proactive_inbox_items(
    db: Session,
    person_id: str,
    *,
    now: datetime | None = None,
) -> int:
    """Persist in-app messages for grounded meaningful changes and necessary questions.

    This intentionally does not create Web Push deliveries. Informational updates
    stay in the LCR; only meaningful changes and explicitly necessary, source-backed
    questions appear in the in-app inbox. Time-sensitive reminders and handoffs use
    their existing notification paths.
    """
    caregivers = db.query(Caregiver).filter(Caregiver.person_id == person_id).all()
    if not caregivers:
        return 0

    timestamp = now or datetime.now(timezone.utc)
    created = 0

    changes = db.query(Change).filter(
        Change.person_id == person_id,
        Change.is_active.is_(True),
        Change.superseded_by_change_id.is_(None),
        Change.attention_candidate.is_(True),
        Change.significance_verdict == SignificanceVerdict.MEANINGFUL_CANDIDATE,
    ).order_by(Change.created_at.asc(), Change.id.asc()).all()
    candidate_event_ids = list(dict.fromkeys(
        str(event_id)
        for change in changes
        for event_id in (change.evidence_ids or [])
        if isinstance(event_id, str) and event_id.strip()
    ))
    valid_event_ids = {
        str(row[0]) for row in (
            db.query(CareEvent.id)
            .filter(CareEvent.person_id == person_id, CareEvent.id.in_(candidate_event_ids))
            .all()
        )
    } if candidate_event_ids else set()

    for change in changes:
        evidence_ids = list(dict.fromkeys(
            str(event_id) for event_id in (change.evidence_ids or [])
            if isinstance(event_id, str) and event_id.strip()
        ))
        # A change is not communicable if even one claimed source is missing or
        # belongs to another care record.
        if not evidence_ids or not set(evidence_ids).issubset(valid_event_ids):
            continue
        period = ""
        if change.comparison_window_start and change.comparison_window_end:
            period = (
                f" for {change.comparison_window_start.date().isoformat()} "
                f"through {change.comparison_window_end.date().isoformat()}"
            )
        body = (
            f"The saved care record shows a possible change{period}. "
            "Review the supporting dates and source entries in the Living Care Record. "
            "This is a record-based observation, not a diagnosis."
        )
        for caregiver in caregivers:
            if _persist_inbox_item(
                db,
                person_id=person_id,
                caregiver_id=caregiver.id,
                category="care_change",
                source_id=change.id,
                title="A change in the care record may be worth reviewing",
                body=body,
                created_at=timestamp,
            ):
                created += 1

    questions = db.query(OpenQuestion).filter(
        OpenQuestion.person_id == person_id,
        OpenQuestion.necessity == "necessary",
        OpenQuestion.status.in_(["open", "unresolved"]),
    ).order_by(OpenQuestion.asked_at.asc(), OpenQuestion.id.asc()).all()
    question_evidence_ids = list(dict.fromkeys(
        str(evidence_id)
        for question in questions
        for evidence_id in (question.evidence_ids or [])
        if isinstance(evidence_id, str) and evidence_id.strip()
    ))
    valid_question_evidence_ids = {
        str(row[0]) for row in (
            db.query(Evidence.id)
            .filter(
                Evidence.person_id == person_id,
                Evidence.id.in_(question_evidence_ids),
            )
            .all()
        )
    } if question_evidence_ids else set()

    for question in questions:
        evidence_ids = list(dict.fromkeys(
            str(evidence_id) for evidence_id in (question.evidence_ids or [])
            if isinstance(evidence_id, str) and evidence_id.strip()
        ))
        if not evidence_ids or not set(evidence_ids).issubset(valid_question_evidence_ids):
            continue
        revision_time = question.updated_at or question.asked_at
        revision = revision_time.isoformat() if revision_time else "1"
        question_text = (question.question or "").strip()
        if not question_text:
            continue
        body = (
            f"SolenOS needs one detail to keep the care record accurate: {question_text} "
            "If you are not sure, say so rather than guessing."
        )
        for caregiver in caregivers:
            if _persist_inbox_item(
                db,
                person_id=person_id,
                caregiver_id=caregiver.id,
                category="care_attention",
                source_id=question.id,
                title="A care detail needs confirmation",
                body=body,
                created_at=timestamp,
                revision=revision,
            ):
                created += 1

    return created

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
    # Communication is downstream of successful reconciliation. A notification
    # persistence failure must not roll back the caregiver's source update.
    try:
        with db.begin_nested():
            create_proactive_inbox_items(db, person_id, now=reference_time)
    except Exception:
        logger.exception("Could not create proactive in-app items for person %s", person_id)
