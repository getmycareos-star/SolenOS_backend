"""Evidence-gated in-app communications built on existing LCR records.

This service creates inbox notifications only. It does not dispatch push, SMS,
email, or make clinical assessments. Existing change significance and explicit
question-necessity assessments are the only inputs that can qualify.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.change_enums import SignificanceVerdict
from app.models.care import Caregiver, Evidence
from app.models.change import Change
from app.models.notifications import CareNotification
from app.models.uncertainty import OpenQuestion


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


def create_proactive_care_notifications(
    db: Session,
    person_id: str,
    *,
    now: datetime | None = None,
) -> int:
    """Create idempotent in-app notifications for grounded changes and necessary questions.

    A change must already be marked as an active meaningful candidate by the
    existing intelligence pipeline and have persisted evidence for this person.
    A question must already have an explicit necessity assessment and persisted
    supporting evidence. Priority by itself never makes a question necessary.
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
            ).first()
            if exists:
                continue
            db.add(CareNotification(
                id=str(uuid.uuid4()),
                person_id=person_id,
                caregiver_id=caregiver.id,
                title="A care change may be worth reviewing",
                body=(
                    "The saved care history contains a meaningful change candidate. "
                    "Review its supporting dates and sources in the Living Care Record. "
                    "This is a record-based observation, not a diagnosis."
                ),
                category="care_change",
                priority="normal",
                source_event_id=change.id,
                created_at=timestamp,
            ))
            created += 1

    questions = db.query(OpenQuestion).filter(
        OpenQuestion.person_id == person_id,
        OpenQuestion.necessity == "necessary",
        OpenQuestion.status.in_(["open", "unresolved"]),
    ).order_by(OpenQuestion.asked_at.asc(), OpenQuestion.id.asc()).all()

    for question in questions:
        evidence_ids = _valid_evidence_ids(db, person_id, question.evidence_ids)
        if not evidence_ids or not (question.necessity_rationale or "").strip():
            continue
        for caregiver in caregivers:
            exists = db.query(CareNotification.id).filter(
                CareNotification.person_id == person_id,
                CareNotification.caregiver_id == caregiver.id,
                CareNotification.category == "care_attention",
                CareNotification.source_event_id == question.id,
            ).first()
            if exists:
                continue
            db.add(CareNotification(
                id=str(uuid.uuid4()),
                person_id=person_id,
                caregiver_id=caregiver.id,
                title="A care detail needs your input",
                body=question.question,
                category="care_attention",
                priority="normal",
                source_event_id=question.id,
                created_at=timestamp,
            ))
            created += 1

    if created:
        db.flush()
    return created
