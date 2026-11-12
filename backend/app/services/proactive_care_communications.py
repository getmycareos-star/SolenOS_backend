"""Evidence-gated in-app messages for material care-record changes.

This creates inbox notifications only after the existing significance pipeline
marks a change meaningful and its source care events are verified as belonging
to the same care recipient. It does not infer clinical urgency.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.change_enums import SignificanceVerdict
from app.models.care import CareEvent, Caregiver
from app.models.change import Change
from app.models.notifications import CareNotification


def create_meaningful_change_notifications(
    db: Session,
    person_id: str,
    *,
    now: datetime | None = None,
) -> int:
    """Create one deterministic in-app notification per caregiver and change."""
    caregivers = db.query(Caregiver).filter(Caregiver.person_id == person_id).all()
    if not caregivers:
        return 0

    changes = db.query(Change).filter(
        Change.person_id == person_id,
        Change.is_active.is_(True),
        Change.superseded_by_change_id.is_(None),
        Change.attention_candidate.is_(True),
        Change.significance_verdict == SignificanceVerdict.MEANINGFUL_CANDIDATE,
    ).order_by(Change.created_at.asc(), Change.id.asc()).all()

    created = 0
    timestamp = now or datetime.now(timezone.utc)
    for change in changes:
        source_ids = [
            str(value) for value in (change.evidence_ids or [])
            if isinstance(value, str) and value.strip()
        ]
        if not source_ids:
            continue
        grounded_ids = {
            row[0] for row in db.query(CareEvent.id).filter(
                CareEvent.person_id == person_id,
                CareEvent.id.in_(source_ids),
            ).all()
        }
        if not grounded_ids:
            continue

        for caregiver in caregivers:
            notification_id = str(uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"solenos:care-change:{person_id}:{caregiver.id}:{change.id}",
            ))
            if db.query(CareNotification.id).filter(
                CareNotification.id == notification_id,
            ).first():
                continue
            db.add(CareNotification(
                id=notification_id,
                person_id=person_id,
                caregiver_id=caregiver.id,
                title="A change in the care record may be worth reviewing",
                body=(
                    "Recent saved care entries differ from the earlier record. "
                    "Open the Living Care Record to review the dates and source entries. "
                    "This is a record-based observation, not a diagnosis."
                ),
                category="care_change",
                priority="normal",
                source_event_id=change.id,
                created_at=timestamp,
            ))
            created += 1

    if created:
        db.flush()
    return created
