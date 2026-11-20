"""Real PostgreSQL proof for shared reminder ownership and delivery routing."""
from datetime import datetime, timedelta, timezone
import os
import uuid

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.skipif(
    not os.getenv("DATABASE_URL"),
    reason="requires migrated PostgreSQL DATABASE_URL",
)

from app.main import app
from app.core.database import SessionLocal
from app.models.notifications import CareNotification
from app.models.privacy_delivery import AuditLogEvent
from app.models.reminder import CareReminder


def test_postgres_reminder_assignment_history_and_due_notification_recipient():
    suffix = uuid.uuid4().hex[:12]
    recipient_email = f"reminder-recipient-{suffix}@example.test"
    client = TestClient(app)

    primary = client.post(
        "/api/v1/sessions/anonymous",
        json={"name": f"Reminder person {suffix}", "caregiver_name": "Primary caregiver"},
    )
    assert primary.status_code == 201, primary.text
    primary_body = primary.json()
    person_id = primary_body["person"]["id"]
    primary_id = primary_body["caregiver"]["id"]
    primary_headers = {"Authorization": f"Bearer {primary_body['access_token']}"}

    invitation = client.post(
        "/api/v1/caregiver-access/invitations",
        headers=primary_headers,
        json={"email": recipient_email, "expires_in_days": 7},
    )
    assert invitation.status_code == 201, invitation.text

    accepted = client.post(
        "/api/v1/caregiver-access/invitations/accept",
        json={
            "token": invitation.json()["invitation_token"],
            "email": recipient_email,
            "password": "postgres-reminder-password-456",
            "name": "Reminder recipient",
        },
    )
    assert accepted.status_code == 201, accepted.text
    recipient = accepted.json()
    recipient_id = recipient["caregiver_id"]
    recipient_headers = {"Authorization": f"Bearer {recipient['access_token']}"}

    evidence = client.post(
        "/api/v1/evidence",
        headers=primary_headers,
        json={
            "person_id": person_id,
            "type": "caregiver_report",
            "source_text": "The clinic asked the family caregiver to call and confirm the appointment details.",
            "uploaded_by_caregiver_id": primary_id,
        },
    )
    assert evidence.status_code == 200, evidence.text
    action_due_at = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    approved_action = client.post(
        f"/api/v1/registered-actions/{person_id}/approve",
        headers=primary_headers,
        json={
            "explicit_approval": True,
            "idempotency_key": f"shared-action-{suffix}",
            "suggestion_id": f"open-loop:shared-action-{suffix}",
            "title": "Call the clinic to confirm the appointment details",
            "action_type": "call",
            "rationale": "The saved caregiver report records a request for a follow-up call.",
            "limitations": "The record does not establish clinical urgency.",
            "evidence_ids": [evidence.json()["id"]],
            "responsible_caregiver_id": recipient_id,
            "create_reminder": True,
            "reminder_due_at": action_due_at,
        },
    )
    assert approved_action.status_code == 201, approved_action.text
    assert approved_action.json()["created_by_caregiver_id"] == primary_id
    assert approved_action.json()["responsible_caregiver_id"] == recipient_id

    with SessionLocal() as db:
        from app.models.action import Action
        action = db.query(Action).filter_by(id=approved_action.json()["id"]).one()
        linked = db.query(CareReminder).filter_by(action_id=action.id).one()
        assert action.responsible_caregiver_id == recipient_id
        assert linked.created_by_caregiver_id == primary_id
        assert linked.assigned_to_caregiver_id == recipient_id
        assignment_audit = db.query(AuditLogEvent).filter_by(
            action="care.action.responsibility_assigned",
            target_type="registered_action",
            target_id=action.id,
        ).one()
        assert assignment_audit.actor_id == primary_id
        assert assignment_audit.record_metadata["responsible_caregiver_id"] == recipient_id

    due_at = datetime(2000, 1, 1, tzinfo=timezone.utc).isoformat()
    created = client.post(
        "/api/v1/reminders",
        headers=primary_headers,
        json={
            "person_id": person_id,
            "title": "Review transport arrangement",
            "note": "Confirm pickup time with the family.",
            "due_at": due_at,
            "assigned_to_caregiver_id": recipient_id,
        },
    )
    assert created.status_code == 201, created.text
    reminder_id = created.json()["id"]
    assert created.json()["created_by_caregiver_id"] == primary_id
    assert created.json()["assigned_to_caregiver_id"] == recipient_id
    assert created.json()["version"] == 1
    assert created.json()["notification_delivery_status"] == "not_created"

    # The actual due-reminder worker writes to the assigned caregiver's inbox.
    with SessionLocal() as db:
        from app.services.reminder_delivery import deliver_due_reminders
        assert deliver_due_reminders(db=db, now=datetime.now(timezone.utc)) >= 1
        notification = db.query(CareNotification).filter_by(
            person_id=person_id,
            source_event_id=reminder_id,
            category="reminder_due",
        ).one()
        assert notification.caregiver_id == recipient_id

    delivered_view = client.get(
        f"/api/v1/reminders/{person_id}",
        headers=primary_headers,
    )
    assert delivered_view.status_code == 200, delivered_view.text
    delivered_item = next(item for item in delivered_view.json() if item["id"] == reminder_id)
    assert delivered_item["notification_delivery_status"] == "in_app_notification_created"

    # The assigned caregiver can reassign responsibility; the old version cannot replay.
    reassigned = client.patch(
        f"/api/v1/reminders/{reminder_id}/assignment",
        headers=recipient_headers,
        json={"assigned_to_caregiver_id": primary_id, "expected_version": 1},
    )
    assert reassigned.status_code == 200, reassigned.text
    assert reassigned.json()["assigned_to_caregiver_id"] == primary_id
    assert reassigned.json()["version"] == 2

    # Reassignment advances the delivery generation so the new responsible
    # caregiver receives a fresh in-app notification for the still-due item.
    with SessionLocal() as db:
        from app.services.reminder_delivery import deliver_due_reminders
        assert deliver_due_reminders(db=db, now=datetime.now(timezone.utc)) >= 1
        new_notification = db.query(CareNotification).filter_by(
            person_id=person_id,
            source_event_id=reminder_id,
            category="reminder_due",
            caregiver_id=primary_id,
            status="active",
        ).one()
        assert new_notification.caregiver_id == primary_id

    stale = client.patch(
        f"/api/v1/reminders/{reminder_id}/assignment",
        headers=primary_headers,
        json={"assigned_to_caregiver_id": recipient_id, "expected_version": 1},
    )
    assert stale.status_code == 409, stale.text

    # Fresh ORM session proves durable reminder state and both attribution events.
    with SessionLocal() as db:
        stored = db.query(CareReminder).filter_by(id=reminder_id).one()
        assert stored.person_id == person_id
        assert stored.created_by_caregiver_id == primary_id
        assert stored.assigned_to_caregiver_id == primary_id
        assert stored.version == 2
        previous_notification = db.query(CareNotification).filter_by(
            person_id=person_id,
            source_event_id=reminder_id,
            category="reminder_due",
            caregiver_id=recipient_id,
        ).one()
        assert previous_notification.status == "resolved"
        assert previous_notification.resolution_reason == "reminder_reassigned"
        current_notification = db.query(CareNotification).filter_by(
            person_id=person_id,
            source_event_id=reminder_id,
            category="reminder_due",
            caregiver_id=primary_id,
            status="active",
        ).one()
        assert current_notification.caregiver_id == primary_id
        events = db.query(AuditLogEvent).filter(
            AuditLogEvent.person_id == person_id,
            AuditLogEvent.target_type == "care_reminder",
            AuditLogEvent.target_id == reminder_id,
        ).order_by(AuditLogEvent.occurred_at.asc()).all()
        assert [event.action for event in events] == [
            "care.reminder.assigned",
            "care.reminder.reassigned",
        ]
        assert events[0].actor_id == primary_id
        assert events[1].actor_id == recipient_id
        assert events[0].record_metadata["assigned_to_caregiver_id"] == recipient_id
        assert events[1].record_metadata["previous_assignee_caregiver_id"] == recipient_id
        assert events[1].record_metadata["assigned_to_caregiver_id"] == primary_id
