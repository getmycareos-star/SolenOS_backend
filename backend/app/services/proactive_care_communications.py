"""Evidence-gated, deduplicated caregiver communication.

This service creates inbox notifications only for existing, source-backed
change assessments and explicitly necessary clarification questions. The
existing notification worker handles opted-in Web Push delivery and quiet
hours; this service never sends directly to a device.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.change_enums import SignificanceVerdict
from app.models.care import CareAssertion, CareEvent, Caregiver, Evidence
from app.models.change import Change
from app.models.notifications import CareNotification
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


def _has_question_source(db: Session, person_id: str, contexts: list[dict]) -> bool:
    for item in contexts:
        if not isinstance(item, dict):
            continue
        source_id = item.get("source_id")
        source_kind = item.get("source_kind")
        if not isinstance(source_id, str) or not source_id.strip():
            continue
        if source_kind == "information_gap":
            found = db.query(InformationGap.id).filter(
                InformationGap.id == source_id,
                InformationGap.person_id == person_id,
            ).first()
        elif source_kind == "contradiction":
            found = db.query(Contradiction.id).filter(
                Contradiction.id == source_id,
                Contradiction.person_id == person_id,
            ).first()
        elif source_kind == "stale_or_unreviewed_assertion":
            found = db.query(CareAssertion.id).filter(
                CareAssertion.id == source_id,
                CareAssertion.person_id == person_id,
            ).first()
        else:
            found = None
        if found is not None:
            return True
    return False


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
    """Create inbox items for meaningful changes and necessary questions.

    - Change notifications require the existing significance engine's
      meaningful-candidate and attention-candidate result and a same-person
      saved care-event/evidence source.
    - Clarification notifications require an open question explicitly assessed
      as necessary and linked to a system source.
    - Repeated reconciliation is idempotent per caregiver and source record.
    - The caller owns the transaction; no external delivery happens here.
    """
    timestamp = now or datetime.now(timezone.utc)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    else:
        timestamp = timestamp.astimezone(timezone.utc)

    caregivers = db.query(Caregiver).filter(Caregiver.person_id == person_id).all()
    if not caregivers:
        return 0

    created = 0
    changes = db.query(Change).filter(
        Change.person_id == person_id,
        Change.is_active.is_(True),
        Change.superseded_by_change_id.is_(None),
        Change.attention_candidate.is_(True),
        Change.significance_verdict == SignificanceVerdict.MEANINGFUL_CANDIDATE,
    ).order_by(Change.created_at.asc(), Change.id.asc()).all()

    for change in changes:
        if not _has_same_person_source(db, person_id, change.evidence_ids or []):
            continue
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
    for question in questions:
        contexts = question.context if isinstance(question.context, list) else []
        if not question.necessity_rationale or not _has_question_source(db, person_id, contexts):
            continue
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

    if created:
        db.flush()
    return created
