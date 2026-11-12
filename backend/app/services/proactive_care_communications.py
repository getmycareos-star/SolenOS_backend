"""Evidence-gated in-app communication for meaningful care-record changes.

This service creates inbox items only. It does not send Web Push, SMS, email,
or clinical advice. Change significance and evidence grounding are decided by
the existing change-intelligence pipeline before this service is called.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.change_enums import SignificanceVerdict
from app.core.uncertainty_enums import GapLifecycleStatus
from app.models.care import Caregiver
from app.models.change import Change
from app.models.notifications import CareNotification
from app.models.uncertainty import InformationGap


def create_attention_change_notifications(
    db: Session,
    person_id: str,
    *,
    now: datetime | None = None,
) -> int:
    """Create one in-app inbox item per caregiver and active attention-worthy change.

    A notification is emitted only when the existing significance engine has
    marked an active change as a meaningful candidate and attached source IDs.
    Reconciliation can call this repeatedly; source-change deduplication keeps
    the inbox stable. The caller owns the transaction.
    """
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
        if not [value for value in (change.evidence_ids or []) if isinstance(value, str) and value.strip()]:
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
                    "The saved care history shows a possible change across recent entries. "
                    "Review the dates and sources in the Living Care Record. "
                    "This is a record-based observation, not a diagnosis."
                ),
                category="care_change",
                priority="normal",
                source_event_id=change.id,
                created_at=timestamp,
            ))
            created += 1

    # Only explicitly critical gaps or high-priority gaps past their declared
    # deadline interrupt the caregiver. Other uncertainty remains in the LCR.
    gaps = db.query(InformationGap).filter(
        InformationGap.person_id == person_id,
        InformationGap.lifecycle_status.in_([
            GapLifecycleStatus.OPEN,
            GapLifecycleStatus.REOPENED,
            GapLifecycleStatus.PARTIALLY_RESOLVED,
        ]),
    ).all()
    for gap in gaps:
        priority = getattr(gap.priority, "value", gap.priority)
        expected_by = gap.expected_by
        if expected_by is not None and expected_by.tzinfo is None:
            expected_by = expected_by.replace(tzinfo=timezone.utc)
        overdue_high = priority == "high" and expected_by is not None and expected_by <= timestamp
        if priority != "critical" and not overdue_high:
            continue
        for caregiver in caregivers:
            exists = db.query(CareNotification.id).filter(
                CareNotification.person_id == person_id,
                CareNotification.caregiver_id == caregiver.id,
                CareNotification.category == "care_attention",
                CareNotification.source_event_id == gap.id,
            ).first()
            if exists:
                continue
            db.add(CareNotification(
                id=str(uuid.uuid4()),
                person_id=person_id,
                caregiver_id=caregiver.id,
                title="A care detail needs your attention",
                body=(
                    "An important care detail is still missing or incomplete. "
                    "Open the Living Care Record to review what needs confirmation."
                ),
                category="care_attention",
                priority="high",
                source_event_id=gap.id,
                created_at=timestamp,
            ))
            created += 1

    if created:
        db.flush()
    return created
