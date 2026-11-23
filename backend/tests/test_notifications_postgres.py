"""PostgreSQL lifecycle acceptance for notification preferences and read receipts.

Uses the migrated PostgreSQL schema and real authenticated HTTP routes. No
SQLite, mocked database session, or external push-provider call is involved.
"""
import os
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL or not DATABASE_URL.startswith(("postgresql://", "postgresql+psycopg2://")),
    reason="requires migrated PostgreSQL DATABASE_URL",
)

from app.core.database import get_db
from app.main import app
from app.models.notifications import CareNotification
from app.models.privacy_delivery import NotificationReadReceipt

engine = create_engine(DATABASE_URL, pool_pre_ping=True) if DATABASE_URL else None
Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False) if engine else None


def test_notification_preferences_and_read_receipts_survive_new_session_and_enforce_scope():
    suffix = uuid.uuid4().hex[:12]
    client = TestClient(app)
    created_ids = []

    def override_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_db
    try:
        first = client.post(
            "/api/v1/sessions/anonymous",
            json={"name": f"Notification recipient {suffix}", "caregiver_name": f"Notification caregiver {suffix}"},
        )
        assert first.status_code == 201, first.text
        first_session = first.json()
        first_headers = {"Authorization": f"Bearer {first_session['access_token']}"}

        account = client.post(
            "/api/v1/accounts",
            headers=first_headers,
            json={"email": f"notification-{suffix}@example.test", "password": f"Notification-password-{suffix}"},
        )
        assert account.status_code == 201, account.text

        initial = client.get("/api/v1/notifications/preferences", headers=first_headers)
        assert initial.status_code == 200, initial.text
        assert initial.json()["quiet_hours_enabled"] is False
        assert initial.json()["reminders_enabled"] is True

        saved = client.put(
            "/api/v1/notifications/preferences",
            headers=first_headers,
            json={
                "quiet_hours_enabled": True,
                "quiet_hours_start": "22:15",
                "quiet_hours_end": "07:30",
                "timezone_name": "Africa/Lagos",
                "reminders_enabled": False,
            },
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["quiet_hours_enabled"] is True
        assert saved.json()["quiet_hours_start"] == "22:15"
        assert saved.json()["quiet_hours_end"] == "07:30"
        assert saved.json()["timezone_name"] == "Africa/Lagos"
        assert saved.json()["reminders_enabled"] is False

        # New bearer session after account login must retrieve the same rows.
        login = client.post(
            "/api/v1/accounts/login",
            json={"email": f"notification-{suffix}@example.test", "password": f"Notification-password-{suffix}"},
        )
        assert login.status_code == 200, login.text
        restored_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        restored = client.get("/api/v1/notifications/preferences", headers=restored_headers)
        assert restored.status_code == 200, restored.text
        assert restored.json()["quiet_hours_enabled"] is True
        assert restored.json()["quiet_hours_start"] == "22:15"
        assert restored.json()["quiet_hours_end"] == "07:30"
        assert restored.json()["timezone_name"] == "Africa/Lagos"
        assert restored.json()["reminders_enabled"] is False

        notification = CareNotification(
            person_id=first_session["person"]["id"],
            caregiver_id=first_session["caregiver"]["id"],
            title=f"PostgreSQL notification {suffix}",
            body="Synthetic persistence acceptance notification.",
            category="care_update",
            priority="normal",
        )
        with Session() as db:
            db.add(notification)
            db.commit()
            db.refresh(notification)
            notification_id = notification.id
        created_ids.append(notification_id)

        feed = client.get("/api/v1/notifications?limit=100&offset=0", headers=restored_headers)
        assert feed.status_code == 200, feed.text
        saved_notification = next(item for item in feed.json()["items"] if item["id"] == notification_id)
        assert saved_notification["is_read"] is False

        marked = client.patch(
            f"/api/v1/notifications/{notification_id}/read",
            headers=restored_headers,
            json={},
        )
        assert marked.status_code == 200, marked.text
        assert marked.json()["is_read"] is True

        with Session() as db:
            receipt = db.query(NotificationReadReceipt).filter_by(
                notification_id=notification_id,
                reader_id=first_session["caregiver"]["id"],
            ).one()
            assert receipt.person_id == first_session["person"]["id"]
            assert receipt.caregiver_id == first_session["caregiver"]["id"]
            assert receipt.read_at is not None

        second = client.post(
            "/api/v1/sessions/anonymous",
            json={"name": f"Other notification recipient {suffix}", "caregiver_name": f"Other caregiver {suffix}"},
        )
        assert second.status_code == 201, second.text
        second_headers = {"Authorization": f"Bearer {second.json()['access_token']}"}
        foreign_feed = client.get("/api/v1/notifications?limit=100&offset=0", headers=second_headers)
        assert foreign_feed.status_code == 200, foreign_feed.text
        assert all(item["id"] != notification_id for item in foreign_feed.json()["items"])
        denied = client.patch(
            f"/api/v1/notifications/{notification_id}/read",
            headers=second_headers,
            json={},
        )
        assert denied.status_code == 404, denied.text

    finally:
        app.dependency_overrides.clear()
        if Session is not None and created_ids:
            with Session() as db:
                db.query(NotificationReadReceipt).filter(NotificationReadReceipt.notification_id.in_(created_ids)).delete(synchronize_session=False)
                db.query(CareNotification).filter(CareNotification.id.in_(created_ids)).delete(synchronize_session=False)
                db.commit()


def test_assigned_reminder_quiet_hours_release_uses_recipient_preference():
    from datetime import datetime, timezone

    from app.models.notifications import CareNotification, CareNotificationPreference
    from app.models.reminder import CareReminder
    from app.services.reminder_delivery import deliver_due_reminders

    suffix = uuid.uuid4().hex[:12]
    client = TestClient(app)

    def override_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_db
    try:
        primary = client.post(
            "/api/v1/sessions/anonymous",
            json={"name": f"Reminder owner {suffix}", "caregiver_name": f"Reminder creator {suffix}"},
        )
        assert primary.status_code == 201, primary.text
        primary_session = primary.json()
        primary_headers = {"Authorization": f"Bearer {primary_session['access_token']}"}

        invitation = client.post(
            "/api/v1/caregiver-access/invitations",
            headers=primary_headers,
            json={"email": f"reminder-assignee-{suffix}@example.test"},
        )
        assert invitation.status_code == 201, invitation.text
        accepted = client.post(
            "/api/v1/caregiver-access/invitations/accept",
            json={
                "token": invitation.json()["invitation_token"],
                "email": f"reminder-assignee-{suffix}@example.test",
                "password": f"Reminder-assignee-{suffix}-password",
                "name": "Assigned caregiver",
            },
        )
        assert accepted.status_code == 201, accepted.text
        assignee = accepted.json()
        assignee_headers = {"Authorization": f"Bearer {assignee['access_token']}"}

        created = client.post(
            "/api/v1/reminders",
            headers=primary_headers,
            json={
                "person_id": primary_session["person"]["id"],
                "title": f"Assigned reminder {suffix}",
                "due_at": datetime(2026, 1, 15, 16, 0, tzinfo=timezone.utc).isoformat(),
                "assigned_to_caregiver_id": assignee["caregiver_id"],
            },
        )
        assert created.status_code == 201, created.text
        reminder_id = created.json()["id"]
        now = datetime(2026, 1, 15, 17, 0, tzinfo=timezone.utc)

        with Session() as db:
            db.add(CareNotificationPreference(
                person_id=primary_session["person"]["id"],
                caregiver_id=assignee["caregiver_id"],
                reminders_enabled=True,
                quiet_hours_enabled=True,
                quiet_hours_start="09:00",
                quiet_hours_end="18:00",
                timezone_name="America/New_York",
            ))
            db.commit()
            assert deliver_due_reminders(db=db, now=now) == 0
            reminder = db.query(CareReminder).filter_by(id=reminder_id).one()
            assert reminder.notification_deferred_until is not None

        changed = client.put(
            "/api/v1/notifications/preferences",
            headers=assignee_headers,
            json={"timezone_name": "Asia/Tokyo"},
        )
        assert changed.status_code == 200, changed.text

        with Session() as db:
            reminder = db.query(CareReminder).filter_by(id=reminder_id).one()
            assert reminder.notification_deferred_until is None
            assert deliver_due_reminders(db=db, now=now) == 1
            notification = db.query(CareNotification).filter_by(
                source_event_id=reminder_id,
                caregiver_id=assignee["caregiver_id"],
                category="reminder_due",
            ).one()
            assert notification.person_id == primary_session["person"]["id"]
    finally:
        app.dependency_overrides.clear()
