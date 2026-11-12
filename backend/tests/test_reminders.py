from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.main as main_module
from app.core.database import Base, get_db, SessionLocal as DefaultSessionLocal
from app.main import app

engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_get_db():
    db = TestSessionLocal()
    try:
        yield db
    finally:
        db.close()


def setup_function():
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = override_get_db
    main_module.SessionLocal = TestSessionLocal


def teardown_function():
    app.dependency_overrides.clear()
    main_module.SessionLocal = DefaultSessionLocal
    Base.metadata.drop_all(bind=engine)


def create_session(client: TestClient, caregiver_name: str):
    response = client.post(
        "/api/v1/sessions/anonymous",
        json={"caregiver_name": caregiver_name},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_reminder_create_list_complete_persists_attribution():
    client = TestClient(app)
    session = create_session(client, "Caregiver one")
    headers = {"Authorization": f"Bearer {session['access_token']}"}
    due_at = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()

    created = client.post(
        "/api/v1/reminders",
        headers=headers,
        json={
            "person_id": session["person"]["id"],
            "title": "Review appointment questions",
            "note": "Check the questions before the visit.",
            "due_at": due_at,
        },
    )
    assert created.status_code == 201, created.text
    reminder = created.json()
    assert reminder["person_id"] == session["person"]["id"]
    assert reminder["created_by_caregiver_id"] == session["caregiver"]["id"]
    assert reminder["status"] == "open"
    assert reminder["delivery_mode"] == "in_app_notification_feed"
    assert reminder["notification_delivery_status"] == "in_app_notification_created"

    listed = client.get(
        f"/api/v1/reminders/{session['person']['id']}",
        headers=headers,
    )
    assert listed.status_code == 200, listed.text
    assert [item["id"] for item in listed.json()] == [reminder["id"]]

    completed = client.patch(
        f"/api/v1/reminders/{reminder['id']}/complete",
        headers=headers,
        json={"completion_note": "Questions reviewed."},
    )
    assert completed.status_code == 200, completed.text
    body = completed.json()
    assert body["status"] == "completed"
    assert body["completed_by_caregiver_id"] == session["caregiver"]["id"]
    assert body["completed_at"] is not None
    assert body["completion_note"] == "Questions reviewed."

    listed_completed = client.get(
        f"/api/v1/reminders/{session['person']['id']}?status=completed",
        headers=headers,
    )
    assert listed_completed.status_code == 200, listed_completed.text
    assert listed_completed.json()[0]["id"] == reminder["id"]


def test_reminder_endpoints_reject_cross_record_access():
    client = TestClient(app)
    first = create_session(client, "Caregiver one")
    second = create_session(client, "Caregiver two")
    first_headers = {"Authorization": f"Bearer {first['access_token']}"}
    second_headers = {"Authorization": f"Bearer {second['access_token']}"}
    due_at = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()

    created = client.post(
        "/api/v1/reminders",
        headers=first_headers,
        json={
            "person_id": first["person"]["id"],
            "title": "Follow up",
            "due_at": due_at,
        },
    )
    assert created.status_code == 201, created.text
    reminder_id = created.json()["id"]

    spoofed_list = client.get(
        f"/api/v1/reminders/{first['person']['id']}",
        headers=second_headers,
    )
    assert spoofed_list.status_code == 403, spoofed_list.text

    spoofed_create = client.post(
        "/api/v1/reminders",
        headers=second_headers,
        json={
            "person_id": first["person"]["id"],
            "title": "Attempted cross-record write",
            "due_at": due_at,
        },
    )
    assert spoofed_create.status_code == 403, spoofed_create.text

    hidden_complete = client.patch(
        f"/api/v1/reminders/{reminder_id}/complete",
        headers=second_headers,
        json={},
    )
    assert hidden_complete.status_code == 404, hidden_complete.text

    hidden_cancel = client.patch(
        f"/api/v1/reminders/{reminder_id}/cancel",
        headers=second_headers,
        json={},
    )
    assert hidden_cancel.status_code == 404, hidden_cancel.text

    still_open = client.get(
        f"/api/v1/reminders/{first['person']['id']}",
        headers=first_headers,
    )
    assert still_open.status_code == 200, still_open.text
    assert still_open.json()[0]["status"] == "open"


def test_reminder_requires_timezone_aware_due_date_and_nonblank_title():
    client = TestClient(app)
    session = create_session(client, "Caregiver")
    headers = {"Authorization": f"Bearer {session['access_token']}"}

    naive = client.post(
        "/api/v1/reminders",
        headers=headers,
        json={
            "person_id": session["person"]["id"],
            "title": "Follow up",
            "due_at": "2026-10-26T12:00:00",
        },
    )
    assert naive.status_code == 422, naive.text

    blank = client.post(
        "/api/v1/reminders",
        headers=headers,
        json={
            "person_id": session["person"]["id"],
            "title": "   ",
            "due_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        },
    )
    assert blank.status_code == 422, blank.text



def test_due_reminders_create_one_in_app_notification_and_are_idempotent():
    from app.models.notifications import CareNotification
    from app.services.reminder_delivery import deliver_due_reminders

    client = TestClient(app)
    session = create_session(client, "Caregiver due reminder")
    headers = {"Authorization": f"Bearer {session['access_token']}"}
    due_at = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    created = client.post(
        "/api/v1/reminders",
        headers=headers,
        json={
            "person_id": session["person"]["id"],
            "title": "Call the clinic",
            "note": "Ask about the appointment time.",
            "due_at": due_at,
        },
    )
    assert created.status_code == 201, created.text
    reminder_id = created.json()["id"]

    db = TestSessionLocal()
    try:
        assert deliver_due_reminders(db=db) == 1
        assert deliver_due_reminders(db=db) == 0
        notifications = db.query(CareNotification).filter(
            CareNotification.source_event_id == reminder_id,
            CareNotification.category == "reminder_due",
        ).all()
        assert len(notifications) == 1
        assert notifications[0].caregiver_id == session["caregiver"]["id"]
        assert notifications[0].title == "Call the clinic"
        assert notifications[0].body == "Ask about the appointment time."
    finally:
        db.close()


def test_due_reminder_delivery_skips_completed_and_disabled_reminders():
    from app.models.notifications import CareNotification, CareNotificationPreference
    from app.models.reminder import CareReminder
    from app.services.reminder_delivery import deliver_due_reminders

    client = TestClient(app)
    session = create_session(client, "Caregiver completed reminder")
    headers = {"Authorization": f"Bearer {session['access_token']}"}
    due_at = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    created = client.post(
        "/api/v1/reminders",
        headers=headers,
        json={"person_id": session["person"]["id"], "title": "Already done", "due_at": due_at},
    )
    assert created.status_code == 201, created.text
    reminder_id = created.json()["id"]
    completed = client.patch(
        f"/api/v1/reminders/{reminder_id}/complete",
        headers=headers,
        json={},
    )
    assert completed.status_code == 200, completed.text

    db = TestSessionLocal()
    try:
        assert deliver_due_reminders(db=db) == 0
        assert db.query(CareNotification).filter(
            CareNotification.source_event_id == reminder_id,
            CareNotification.category == "reminder_due",
        ).count() == 0
    finally:
        db.close()

    second = create_session(client, "Caregiver disabled reminders")
    second_headers = {"Authorization": f"Bearer {second['access_token']}"}
    disabled = client.post(
        "/api/v1/reminders",
        headers=second_headers,
        json={
            "person_id": second["person"]["id"],
            "title": "Notifications disabled",
            "due_at": due_at,
        },
    )
    assert disabled.status_code == 201, disabled.text
    db = TestSessionLocal()
    try:
        db.add(CareNotificationPreference(
            person_id=second["person"]["id"],
            caregiver_id=second["caregiver"]["id"],
            reminders_enabled=False,
            quiet_hours_enabled=False,
        ))
        db.commit()
        assert deliver_due_reminders(db=db) == 0
        assert db.query(CareNotification).filter(
            CareNotification.source_event_id == disabled.json()["id"],
            CareNotification.category == "reminder_due",
        ).count() == 0
    finally:
        db.close()


def test_due_reminder_batch_does_not_starve_older_delivered_rows():
    from app.models.notifications import CareNotification
    from app.models.reminder import CareReminder
    from app.services.reminder_delivery import deliver_due_reminders

    client = TestClient(app)
    session = create_session(client, "Caregiver batch")
    due_at = datetime.now(timezone.utc) - timedelta(minutes=2)
    db = TestSessionLocal()
    try:
        reminders = [
            CareReminder(
                person_id=session["person"]["id"],
                created_by_caregiver_id=session["caregiver"]["id"],
                title=f"Follow up {index}",
                due_at=due_at,
                status="open",
            )
            for index in range(101)
        ]
        db.add_all(reminders)
        db.commit()

        assert deliver_due_reminders(db=db, now=datetime.now(timezone.utc)) == 100
        assert deliver_due_reminders(db=db, now=datetime.now(timezone.utc)) == 1
        assert db.query(CareNotification).filter(
            CareNotification.caregiver_id == session["caregiver"]["id"],
            CareNotification.category == "reminder_due",
        ).count() == 101
    finally:
        db.close()


def test_quiet_hours_defer_due_reminder_then_release_it_without_changing_due_time():
    from app.models.notifications import CareNotification, CareNotificationPreference
    from app.models.reminder import CareReminder
    from app.services.reminder_delivery import deliver_due_reminders

    client = TestClient(app)
    session = create_session(client, "Caregiver quiet hours")
    db = TestSessionLocal()
    try:
        original_due = datetime(2026, 1, 15, 6, 59, tzinfo=timezone.utc)
        reminder = CareReminder(
            person_id=session["person"]["id"],
            created_by_caregiver_id=session["caregiver"]["id"],
            title="Check care plan",
            due_at=original_due,
            status="open",
        )
        db.add(reminder)
        db.add(CareNotificationPreference(
            person_id=session["person"]["id"],
            caregiver_id=session["caregiver"]["id"],
            reminders_enabled=True,
            quiet_hours_enabled=True,
            quiet_hours_start="21:00",
            quiet_hours_end="08:00",
            timezone_name="America/New_York",
        ))
        db.commit()

        # 02:00 in New York: the notification is deferred until quiet hours end.
        assert deliver_due_reminders(db=db, now=datetime(2026, 1, 15, 7, 0, tzinfo=timezone.utc)) == 0
        db.refresh(reminder)
        stored_due = reminder.due_at.replace(tzinfo=timezone.utc) if reminder.due_at.tzinfo is None else reminder.due_at
        deferred = reminder.notification_deferred_until
        deferred = deferred.replace(tzinfo=timezone.utc) if deferred and deferred.tzinfo is None else deferred
        assert stored_due == original_due
        assert deferred == datetime(2026, 1, 15, 13, 0, tzinfo=timezone.utc)
        assert db.query(CareNotification).filter(CareNotification.source_event_id == reminder.id).count() == 0

        # 10:00 in New York: delivery resumes, without changing the care reminder due time.
        assert deliver_due_reminders(db=db, now=datetime(2026, 1, 15, 15, 0, tzinfo=timezone.utc)) == 1
        db.refresh(reminder)
        stored_due = reminder.due_at.replace(tzinfo=timezone.utc) if reminder.due_at.tzinfo is None else reminder.due_at
        assert stored_due == original_due
        assert db.query(CareNotification).filter(CareNotification.source_event_id == reminder.id).count() == 1
    finally:
        db.close()


def test_reminder_snooze_moves_due_time_and_records_caregiver_without_completing():
    client = TestClient(app)
    session = create_session(client, "Caregiver snooze")
    headers = {"Authorization": f"Bearer {session['access_token']}"}
    due_at = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    created = client.post(
        "/api/v1/reminders",
        headers=headers,
        json={"person_id": session["person"]["id"], "title": "Follow up tomorrow", "due_at": due_at},
    )
    assert created.status_code == 201, created.text
    reminder_id = created.json()["id"]
    snooze_until = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    snoozed = client.patch(
        f"/api/v1/reminders/{reminder_id}/snooze",
        headers=headers,
        json={"snooze_until": snooze_until},
    )
    assert snoozed.status_code == 200, snoozed.text
    body = snoozed.json()
    assert body["status"] == "open"
    assert body["snoozed_by_caregiver_id"] == session["caregiver"]["id"]
    assert body["snoozed_at"] is not None
    assert body["snooze_count"] == 1
    assert abs((datetime.fromisoformat(body["due_at"]) - datetime.fromisoformat(snooze_until)).total_seconds()) < 1
    from app.models.reminder import CareReminder
    db = TestSessionLocal()
    try:
        stored = db.query(CareReminder).filter_by(id=reminder_id).one()
        assert stored.delivery_generation == 1
        assert stored.status == "open"
    finally:
        db.close()


def test_snooze_rejects_completed_reminder_and_cross_record_access():
    client = TestClient(app)
    first = create_session(client, "First snooze caregiver")
    second = create_session(client, "Second snooze caregiver")
    first_headers = {"Authorization": f"Bearer {first['access_token']}"}
    second_headers = {"Authorization": f"Bearer {second['access_token']}"}
    created = client.post(
        "/api/v1/reminders",
        headers=first_headers,
        json={
            "person_id": first["person"]["id"],
            "title": "Do not snooze after completion",
            "due_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        },
    )
    reminder_id = created.json()["id"]
    hidden = client.patch(
        f"/api/v1/reminders/{reminder_id}/snooze",
        headers=second_headers,
        json={"snooze_until": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()},
    )
    assert hidden.status_code == 404
    completed = client.patch(
        f"/api/v1/reminders/{reminder_id}/complete",
        headers=first_headers,
        json={},
    )
    assert completed.status_code == 200
    rejected = client.patch(
        f"/api/v1/reminders/{reminder_id}/snooze",
        headers=first_headers,
        json={"snooze_until": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()},
    )
    assert rejected.status_code == 409


def test_quiet_hours_use_caregiver_timezone_and_overnight_window():
    from types import SimpleNamespace
    from app.services.reminder_delivery import is_quiet_hours

    preference = SimpleNamespace(
        quiet_hours_enabled=True,
        quiet_hours_start="21:00",
        quiet_hours_end="08:00",
        timezone_name="America/New_York",
    )
    # 02:00 local in New York during standard time.
    assert is_quiet_hours(preference, datetime(2026, 1, 15, 7, 0, tzinfo=timezone.utc)) is True
    # 10:00 local is outside the configured overnight quiet window.
    assert is_quiet_hours(preference, datetime(2026, 1, 15, 15, 0, tzinfo=timezone.utc)) is False



def test_snooze_complete_and_cancel_stop_queued_push_retries():
    from app.models.notifications import CareNotification, CarePushDelivery, CarePushSubscription

    client = TestClient(app)
    session = create_session(client, "Reminder push cancellation")
    headers = {"Authorization": f"Bearer {session['access_token']}"}
    db = TestSessionLocal()
    try:
        subscription = CarePushSubscription(
            id="push-subscription-test",
            person_id=session["person"]["id"],
            caregiver_id=session["caregiver"]["id"],
            endpoint_hash="a" * 64,
            encrypted_subscription="encrypted-test-subscription",
        )
        db.add(subscription)
        db.commit()

        reminder_ids = []
        for index in range(3):
            created = client.post(
                "/api/v1/reminders",
                headers=headers,
                json={
                    "person_id": session["person"]["id"],
                    "title": f"Reminder {index}",
                    "due_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
                },
            )
            assert created.status_code == 201, created.text
            reminder_id = created.json()["id"]
            reminder_ids.append(reminder_id)
            notification = CareNotification(
                id=f"reminder-due:{reminder_id}",
                person_id=session["person"]["id"],
                caregiver_id=session["caregiver"]["id"],
                title=f"Reminder {index}",
                body="Due",
                category="reminder_due",
                source_event_id=reminder_id,
            )
            db.add(notification)
            db.commit()
            db.add(CarePushDelivery(
                id=f"push-delivery-{index}",
                notification_id=notification.id,
                subscription_id=subscription.id,
                status="pending",
                attempt_count=2,
                next_attempt_at=datetime.now(timezone.utc),
            ))
            db.commit()

        snoozed = client.patch(
            f"/api/v1/reminders/{reminder_ids[0]}/snooze",
            headers=headers,
            json={"snooze_until": (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()},
        )
        assert snoozed.status_code == 200, snoozed.text
        completed = client.patch(
            f"/api/v1/reminders/{reminder_ids[1]}/complete",
            headers=headers,
            json={},
        )
        assert completed.status_code == 200, completed.text
        cancelled = client.patch(
            f"/api/v1/reminders/{reminder_ids[2]}/cancel",
            headers=headers,
        )
        assert cancelled.status_code == 200, cancelled.text

        db.expire_all()
        statuses = {
            row.notification_id: row.status
            for row in db.query(CarePushDelivery).all()
        }
        assert statuses == {
            f"reminder-due:{reminder_ids[0]}": "cancelled",
            f"reminder-due:{reminder_ids[1]}": "cancelled",
            f"reminder-due:{reminder_ids[2]}": "cancelled",
        }
    finally:
        db.close()



def test_disabled_reminder_batch_does_not_starve_enabled_caregiver():
    from app.models.notifications import CareNotification, CareNotificationPreference
    from app.models.reminder import CareReminder
    from app.services.reminder_delivery import deliver_due_reminders

    client = TestClient(app)
    disabled = create_session(client, "Disabled reminders caregiver")
    enabled = create_session(client, "Enabled reminders caregiver")
    due_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    db = TestSessionLocal()
    try:
        db.add(CareNotificationPreference(
            person_id=disabled["person"]["id"],
            caregiver_id=disabled["caregiver"]["id"],
            reminders_enabled=False,
            quiet_hours_enabled=False,
        ))
        db.add_all([
            CareReminder(
                person_id=disabled["person"]["id"],
                created_by_caregiver_id=disabled["caregiver"]["id"],
                title=f"Disabled {index}",
                due_at=due_at,
                status="open",
            )
            for index in range(101)
        ])
        enabled_reminder = CareReminder(
            person_id=enabled["person"]["id"],
            created_by_caregiver_id=enabled["caregiver"]["id"],
            title="Enabled reminder",
            due_at=due_at + timedelta(seconds=1),
            status="open",
        )
        db.add(enabled_reminder)
        db.commit()

        assert deliver_due_reminders(db=db, now=datetime.now(timezone.utc)) == 0
        assert deliver_due_reminders(db=db, now=datetime.now(timezone.utc)) == 1
        assert db.query(CareNotification).filter(
            CareNotification.source_event_id == enabled_reminder.id
        ).count() == 1
    finally:
        db.close()


def test_web_push_public_key_is_not_advertised_when_private_key_is_missing(monkeypatch):
    from types import SimpleNamespace
    from app.api import push_notifications

    client = TestClient(app)
    session = create_session(client, "Push configuration caregiver")
    headers = {"Authorization": f"Bearer {session['access_token']}"}
    monkeypatch.setattr(
        push_notifications,
        "get_settings",
        lambda: SimpleNamespace(
            vapid_public_key="public-key",
            vapid_private_key="",
            vapid_subject="mailto:support@solenosai.net",
        ),
    )
    response = client.get("/api/v1/notifications/push/public-key", headers=headers)
    assert response.status_code == 503, response.text


def test_web_push_public_key_requires_valid_vapid_subject(monkeypatch):
    from types import SimpleNamespace
    from app.api import push_notifications

    client = TestClient(app)
    session = create_session(client, "Push subject caregiver")
    headers = {"Authorization": f"Bearer {session['access_token']}"}
    monkeypatch.setattr(
        push_notifications,
        "get_settings",
        lambda: SimpleNamespace(
            vapid_public_key="public-key",
            vapid_private_key="private-key",
            vapid_subject="support@solenosai.net",
        ),
    )
    response = client.get("/api/v1/notifications/push/public-key", headers=headers)
    assert response.status_code == 503, response.text


def test_timezone_preference_change_releases_stale_quiet_hours_deferral():
    from app.models.notifications import CareNotification, CareNotificationPreference
    from app.models.reminder import CareReminder
    from app.services.reminder_delivery import deliver_due_reminders

    client = TestClient(app)
    session = create_session(client, "Timezone change caregiver")
    headers = {"Authorization": f"Bearer {session['access_token']}"}
    now = datetime(2026, 1, 15, 17, 0, tzinfo=timezone.utc)
    reminder = CareReminder(
        person_id=session["person"]["id"],
        created_by_caregiver_id=session["caregiver"]["id"],
        title="Timezone change follow-up",
        due_at=datetime(2026, 1, 15, 16, 0, tzinfo=timezone.utc),
        status="open",
    )
    db = TestSessionLocal()
    try:
        db.add(reminder)
        db.add(CareNotificationPreference(
            person_id=session["person"]["id"],
            caregiver_id=session["caregiver"]["id"],
            reminders_enabled=True,
            quiet_hours_enabled=True,
            quiet_hours_start="09:00",
            quiet_hours_end="18:00",
            timezone_name="America/New_York",
        ))
        db.commit()

        # Noon in New York is inside the configured quiet window.
        assert deliver_due_reminders(db=db, now=now) == 0
        db.refresh(reminder)
        assert reminder.notification_deferred_until is not None
    finally:
        db.close()

    changed = client.put(
        "/api/v1/notifications/preferences",
        headers=headers,
        json={"timezone_name": "Asia/Tokyo"},
    )
    assert changed.status_code == 200, changed.text
    db = TestSessionLocal()
    try:
        db.refresh(reminder)
        assert reminder.notification_deferred_until is None
        # 02:00 in Tokyo is outside the configured 09:00-18:00 quiet window.
        assert deliver_due_reminders(db=db, now=now) == 1
        assert db.query(CareNotification).filter(
            CareNotification.source_event_id == reminder.id,
            CareNotification.category == "reminder_due",
        ).count() == 1
    finally:
        db.close()


def test_push_subscription_endpoint_cannot_be_claimed_by_another_care_record(monkeypatch):
    from types import SimpleNamespace
    from app.api import push_notifications

    monkeypatch.setattr(
        push_notifications,
        "get_settings",
        lambda: SimpleNamespace(
            vapid_public_key="public-key",
            vapid_private_key="private-key",
            vapid_subject="mailto:support@solenosai.net",
            secret_key="test-secret-key-for-push-subscription-isolation",
        ),
    )
    client = TestClient(app)
    first = create_session(client, "Push subscription owner")
    second = create_session(client, "Push subscription attacker")
    payload = {
        "endpoint": "https://fcm.googleapis.com/fcm/send/solenos-test-subscription",
        "keys": {"p256dh": "test-public-key", "auth": "test-auth-secret"},
    }
    first_response = client.post(
        "/api/v1/notifications/push/subscriptions",
        headers={"Authorization": f"Bearer {first['access_token']}"},
        json=payload,
    )
    assert first_response.status_code == 201, first_response.text

    cross_record = client.post(
        "/api/v1/notifications/push/subscriptions",
        headers={"Authorization": f"Bearer {second['access_token']}"},
        json=payload,
    )
    assert cross_record.status_code == 409, cross_record.text
