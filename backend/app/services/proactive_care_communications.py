"""Evidence-gated, deduplicated caregiver communication.

Creates inbox items only for existing, source-backed meaningful changes and
explicitly necessary clarification questions. The existing notification worker
handles opted-in Web Push delivery and quiet hours; this service never sends
directly to a device.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.change_enums import SignificanceVerdict
from app.models.care import CareEvent, Caregiver, Evidence, Person
from app.models.care_continuity import CareAssertion
from app.models.change import Change
from app.models.notifications import CareNotification, CarePushDelivery
from app.models.uncertainty import Contradiction, InformationGap, OpenQuestion


def _has_same_person_source(db: Session, person_id: str, source_ids: list[str]) -> bool:
    ids = {value.strip() for value in source_ids if isinstance(value, str) and value.strip()}
    if not ids:
        return False
    event = db.query(CareEvent.id).filter(
        CareEvent.person_id == person_id,
        CareEvent.id.in_(ids),
    ).first()
    if event is not None:
        return True
    evidence = db.query(Evidence.id).filter(
        Evidence.person_id == person_id,
        Evidence.id.in_(ids),
    ).first()
    return evidence is not None


def _has_necessary_question_source(db: Session, question: OpenQuestion, person_id: str) -> bool:
    if not question.necessity_rationale or not _has_same_person_source(
        db, person_id, list(question.evidence_ids or [])
    ):
        return False

    contexts = question.context if isinstance(question.context, list) else []
    for item in contexts:
        if not isinstance(item, dict):
            continue
        source_id = item.get("source_id")
        source_kind = item.get("source_kind")
        assessment = item.get("necessity_assessment")
        if not isinstance(source_id, str) or not source_id.strip() or not isinstance(assessment, dict):
            continue
        if (
            assessment.get("necessity") != "necessary"
            or not isinstance(assessment.get("necessity_rationale"), str)
            or not assessment["necessity_rationale"].strip()
            or not isinstance(assessment.get("decision_dependency"), str)
            or not assessment["decision_dependency"].strip()
        ):
            continue

        if source_kind == "information_gap":
            source = db.query(InformationGap).filter(
                InformationGap.id == source_id,
                InformationGap.person_id == person_id,
            ).first()
        elif source_kind == "contradiction":
            source = db.query(Contradiction).filter(
                Contradiction.id == source_id,
                Contradiction.person_id == person_id,
            ).first()
        elif source_kind == "stale_or_unreviewed_assertion":
            source = db.query(CareAssertion).filter(
                CareAssertion.id == source_id,
                CareAssertion.person_id == person_id,
            ).first()
        else:
            source = None
        if source is not None and _has_same_person_source(
            db, person_id, list(getattr(source, "evidence_ids", None) or [])
        ):
            return True
    return False


def _cancel_pending_pushes(db: Session, notification_id: str) -> None:
    pending = db.query(CarePushDelivery).filter(
        CarePushDelivery.notification_id == notification_id,
        CarePushDelivery.status == "pending",
    ).all()
    for delivery in pending:
        delivery.status = "cancelled"


def _create_notification(
    db: Session,
    *,
    person_id: str,
    caregiver_id: str,
    category: str,
    source_event_id: str,
    title: str,
    body: str,
    priority: str,
    created_at: datetime,
) -> bool:
    exists = db.query(CareNotification.id).filter(
        CareNotification.person_id == person_id,
        CareNotification.caregiver_id == caregiver_id,
        CareNotification.category == category,
        CareNotification.source_event_id == source_event_id,
    ).first()
    if exists:
        return False
    db.add(CareNotification(
        id=str(uuid.uuid4()),
        person_id=person_id,
        caregiver_id=caregiver_id,
        title=title,
        body=body,
        category=category,
        priority=priority,
        source_event_id=source_event_id,
        created_at=created_at,
    ))
    return True


def create_proactive_care_notifications(
    db: Session,
    person_id: str,
    *,
    now: datetime | None = None,
) -> int:
    """Create or update inbox items after evidence and necessity reconciliation.

    Meaningful-change messages require the existing significance assessment,
    an active change, and a same-person care-event/evidence source.
    Clarification messages require an open question, explicit necessity,
    a recorded rationale and decision dependency, and a same-person source.
    Reconciliation is idempotent per caregiver/source. Obsolete notifications
    are converted to non-actionable updates and pending push deliveries cancelled.
    The caller owns the transaction.
    """
    timestamp = now or datetime.now(timezone.utc)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    else:
        timestamp = timestamp.astimezone(timezone.utc)

    # Serialize per person so concurrent reconciliation cannot race deduplication.
    db.query(Person.id).filter(Person.id == person_id).with_for_update().first()
    caregivers = db.query(Caregiver).filter(Caregiver.person_id == person_id).all()
    if not caregivers:
        return 0

    active_change_ids: set[str] = set()
    changes = db.query(Change).filter(
        Change.person_id == person_id,
        Change.is_active.is_(True),
        Change.superseded_by_change_id.is_(None),
        Change.attention_candidate.is_(True),
        Change.significance_verdict == SignificanceVerdict.MEANINGFUL_CANDIDATE,
    ).order_by(Change.created_at.asc(), Change.id.asc()).all()

    created = 0
    for change in changes:
        if not _has_same_person_source(db, person_id, list(change.evidence_ids or [])):
            continue
        active_change_ids.add(change.id)
        for caregiver in caregivers:
            created += int(_create_notification(
                db,
                person_id=person_id,
                caregiver_id=caregiver.id,
                category="care_change",
                source_event_id=change.id,
                title="A care change may be worth reviewing",
                body=(
                    "The saved care history shows a possible change across recent entries. "
                    "Review the dates and sources in the Living Care Record. "
                    "This is a record-based observation, not a diagnosis."
                ),
                priority="normal",
                created_at=timestamp,
            ))

    questions = db.query(OpenQuestion).filter(
        OpenQuestion.person_id == person_id,
        OpenQuestion.status.in_(["open", "unresolved"]),
        OpenQuestion.necessity == "necessary",
    ).all()
    active_question_ids: set[str] = set()
    for question in questions:
        if not _has_necessary_question_source(db, question, person_id):
            continue
        active_question_ids.add(question.id)
        for caregiver in caregivers:
            created += int(_create_notification(
                db,
                person_id=person_id,
                caregiver_id=caregiver.id,
                category="care_attention",
                source_event_id=question.id,
                title="A care detail needs your attention",
                body=(
                    "SolenOS needs one important care detail confirmed. "
                    "Open the Living Care Record to see the question and the information already checked."
                ),
                priority="high",
                created_at=timestamp,
            ))

    # Do not leave old messages looking actionable after their source is resolved,
    # superseded, or no longer meets the necessity gate.
    old_items = db.query(CareNotification).filter(
        CareNotification.person_id == person_id,
        CareNotification.category.in_(["care_change", "care_attention"]),
    ).all()
    for item in old_items:
        if item.category == "care_change" and item.source_event_id not in active_change_ids:
            item.category = "care_change_superseded"
            item.title = "A newer care update is available"
            item.body = (
                "A later care update replaced an earlier change assessment. "
                "Review the latest care history in the Living Care Record."
            )
            item.priority = "normal"
            _cancel_pending_pushes(db, item.id)
        elif item.category == "care_attention" and item.source_event_id not in active_question_ids:
            item.category = "care_attention_updated"
            item.title = "This question no longer needs your attention"
            item.body = (
                "SolenOS updated this question after reviewing the linked care information. "
                "It remains in the record, but no answer is needed right now."
            )
            item.priority = "normal"
            _cancel_pending_pushes(db, item.id)

    if created or old_items:
        db.flush()
    return created
