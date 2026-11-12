"""Evidence-gated, lifecycle-managed in-app care communications.

Notifications are projections of committed care-reconciliation decisions. This
service never makes clinical assessments and never dispatches to a provider.
The existing reminder delivery worker handles push delivery after commit.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.change_enums import SignificanceVerdict
from app.models.care import Caregiver, Evidence
from app.models.change import Change
from app.models.notifications import CareNotification, CarePushDelivery
from app.models.uncertainty import OpenQuestion
from app.services.care_continuity import has_valid_caregiver_necessity


OPEN_QUESTION_STATUSES = {"open", "unanswered", "pending", "unresolved"}


def _display_date(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.date().isoformat()


def _evidence_source_summary(db: Session, person_id: str, evidence_ids: list[str] | None) -> str:
    valid_ids = _valid_evidence_ids(db, person_id, evidence_ids)
    if not valid_ids:
        return ""
    rows = db.query(Evidence).filter(
        Evidence.person_id == person_id,
        Evidence.id.in_(valid_ids),
    ).all()
    labels = []
    for row in rows:
        kind = (row.type or row.source_type or "care record").strip().casefold()
        label = {
            "daily_care": "Daily Care entry",
            "care_event": "Daily Care entry",
            "document": "document",
            "appointment": "appointment record",
            "handoff": "care handoff",
        }.get(kind, kind.replace("_", " ") or "care record")
        source_date = _display_date(row.occurred_at or row.uploaded_at)
        labels.append(f"{label} ({source_date})" if source_date else label)
    labels = list(dict.fromkeys(labels))
    if len(labels) > 3:
        return ", ".join(labels[:3]) + f", and {len(labels) - 3} more saved sources"
    return ", ".join(labels)


def _care_change_message(db: Session, person_id: str, change: Change, evidence_ids: list[str]) -> str:
    subject = (change.subject_type or "").replace("_", " ").strip()
    if not subject or subject.casefold() in {"person", "care"}:
        subject = "care information"
    start = _display_date(change.comparison_window_start)
    end = _display_date(change.comparison_window_end)
    period = f" from {start} to {end}" if start and end else (
        f" on {start or end}" if start or end else ""
    )
    sources = _evidence_source_summary(db, person_id, evidence_ids)
    source_sentence = f" Supporting records: {sources}." if sources else ""
    return (
        f"The saved care record shows a possible change in {subject}{period}."
        f"{source_sentence} Review the entries in your Living Care Record. "
        "This is an observation from saved records, not a diagnosis."
    )


def _necessary_question_message(db: Session, person_id: str, question: OpenQuestion) -> str:
    sources = _evidence_source_summary(db, person_id, question.evidence_ids)
    source_sentence = f" Supporting records: {sources}." if sources else ""
    has_conflict = any(
        isinstance(item, dict) and item.get("source_kind") == "contradiction"
        for item in (question.context or [])
    )
    conflict_sentence = (
        " The reports remain separate; SolenOS has not selected one as correct."
        if has_conflict else ""
    )
    return (
        f"{question.question}{source_sentence} If you're not sure, you can say so."
        f"{conflict_sentence}"
    )


def _valid_evidence_ids(db: Session, person_id: str, values: list[str] | None) -> list[str]:
    candidates = list(dict.fromkeys(
        str(value).strip() for value in (values or [])
        if isinstance(value, str) and value.strip()
    ))
    if not candidates:
        return []
    found = {
        str(row[0]) for row in db.query(Evidence.id).filter(
            Evidence.person_id == person_id,
            Evidence.id.in_(candidates),
        ).all()
    }
    return [value for value in candidates if value in found]


def _active_change(db: Session, person_id: str, source_id: str | None) -> tuple[bool, str, str | None]:
    change = db.query(Change).filter(
        Change.id == source_id,
        Change.person_id == person_id,
    ).first() if source_id else None
    if change is None:
        return False, "resolved", "source_unavailable"
    if change.superseded_by_change_id:
        return False, "superseded", "source_superseded"
    if not (
        change.is_active is True
        and change.attention_candidate is True
        and change.significance_verdict == SignificanceVerdict.MEANINGFUL_CANDIDATE
    ):
        return False, "resolved", "change_no_longer_meaningful"
    if not _valid_evidence_ids(db, person_id, change.evidence_ids):
        return False, "resolved", "supporting_evidence_unavailable"
    return True, "active", None


def _active_question(db: Session, person_id: str, source_id: str | None) -> tuple[bool, str]:
    question = db.query(OpenQuestion).filter(
        OpenQuestion.id == source_id,
        OpenQuestion.person_id == person_id,
    ).first() if source_id else None
    if question is None:
        return False, "source_unavailable"
    if question.status not in OPEN_QUESTION_STATUSES:
        return False, "question_resolved"
    if not has_valid_caregiver_necessity(question, db, person_id):
        return False, "necessity_or_evidence_no_longer_valid"
    return True, ""


def _cancel_pending_pushes(db: Session, notification_id: str, reason: str) -> None:
    db.query(CarePushDelivery).filter(
        CarePushDelivery.notification_id == notification_id,
        CarePushDelivery.status == "pending",
    ).update(
        {
            CarePushDelivery.status: "cancelled",
            CarePushDelivery.last_error_code: reason[:64],
            CarePushDelivery.last_error_message: "The care source no longer requires this notification.",
        },
        synchronize_session=False,
    )


def _close_obsolete_notifications(db: Session, person_id: str, timestamp: datetime) -> int:
    """Resolve or supersede active messages when their source state changes."""
    active = db.query(CareNotification).filter(
        CareNotification.person_id == person_id,
        CareNotification.status == "active",
        CareNotification.category.in_(["care_change", "care_attention"]),
    ).order_by(CareNotification.created_at.asc(), CareNotification.id.asc()).all()
    closed = 0
    for notification in active:
        if notification.category == "care_change":
            still_active, next_status, reason = _active_change(
                db, person_id, notification.source_event_id
            )
            if still_active:
                continue
            notification.status = next_status
            notification.resolution_reason = reason
        else:
            still_active, reason = _active_question(
                db, person_id, notification.source_event_id
            )
            if still_active:
                continue
            notification.status = "resolved"
            notification.resolution_reason = reason
        notification.resolved_at = timestamp
        notification.updated_at = timestamp
        _cancel_pending_pushes(db, notification.id, notification.resolution_reason or "source_resolved")
        closed += 1
    return closed


def _create_active_notification(db: Session, notification: CareNotification) -> bool:
    """Use the partial unique index as the concurrency-safe idempotency gate."""
    try:
        with db.begin_nested():
            db.add(notification)
            db.flush()
        return True
    except IntegrityError:
        # Treat only the expected active-source uniqueness race as a no-op.
        duplicate = db.query(CareNotification.id).filter(
            CareNotification.person_id == notification.person_id,
            CareNotification.caregiver_id == notification.caregiver_id,
            CareNotification.category == notification.category,
            CareNotification.source_event_id == notification.source_event_id,
            CareNotification.status == "active",
        ).first()
        if duplicate:
            return False
        raise


def create_proactive_care_notifications(
    db: Session,
    person_id: str,
    *,
    now: datetime | None = None,
) -> int:
    """Synchronize active notifications with meaningful changes and necessary questions.

    A change requires an active meaningful candidate and person-scoped evidence.
    A question requires a valid necessity rationale, decision dependency, and
    same-recipient evidence. Closing or superseding a source closes its message
    and cancels any push that has not yet been attempted.
    """
    caregivers = db.query(Caregiver).filter(Caregiver.person_id == person_id).all()
    if not caregivers:
        return 0

    timestamp = now or datetime.now(timezone.utc)
    _close_obsolete_notifications(db, person_id, timestamp)
    created = 0

    changes = db.query(Change).filter(
        Change.person_id == person_id,
        Change.is_active.is_(True),
        Change.superseded_by_change_id.is_(None),
        Change.attention_candidate.is_(True),
        Change.significance_verdict == SignificanceVerdict.MEANINGFUL_CANDIDATE,
    ).order_by(Change.created_at.asc(), Change.id.asc()).all()

    for change in changes:
        evidence_ids = _valid_evidence_ids(db, person_id, change.evidence_ids)
        if not evidence_ids:
            continue
        for caregiver in caregivers:
            exists = db.query(CareNotification.id).filter(
                CareNotification.person_id == person_id,
                CareNotification.caregiver_id == caregiver.id,
                CareNotification.category == "care_change",
                CareNotification.source_event_id == change.id,
                CareNotification.status == "active",
            ).first()
            if exists:
                continue
            created += int(_create_active_notification(db, CareNotification(
                id=str(uuid.uuid4()),
                person_id=person_id,
                caregiver_id=caregiver.id,
                title="A care change may be worth reviewing",
                body=_care_change_message(db, person_id, change, evidence_ids),
                category="care_change",
                priority="normal",
                source_event_id=change.id,
                status="active",
                created_at=timestamp,
                updated_at=timestamp,
            )))

    questions = db.query(OpenQuestion).filter(
        OpenQuestion.person_id == person_id,
        OpenQuestion.necessity == "necessary",
        OpenQuestion.status.in_(sorted(OPEN_QUESTION_STATUSES)),
    ).order_by(OpenQuestion.asked_at.asc(), OpenQuestion.id.asc()).all()

    for question in questions:
        if not has_valid_caregiver_necessity(question, db, person_id):
            continue
        for caregiver in caregivers:
            exists = db.query(CareNotification.id).filter(
                CareNotification.person_id == person_id,
                CareNotification.caregiver_id == caregiver.id,
                CareNotification.category == "care_attention",
                CareNotification.source_event_id == question.id,
                CareNotification.status == "active",
            ).first()
            if exists:
                continue
            created += int(_create_active_notification(db, CareNotification(
                id=str(uuid.uuid4()),
                person_id=person_id,
                caregiver_id=caregiver.id,
                title="A care detail needs your input",
                body=_necessary_question_message(db, person_id, question),
                category="care_attention",
                priority="normal",
                source_event_id=question.id,
                status="active",
                created_at=timestamp,
                updated_at=timestamp,
            )))

    db.flush()
    return created
