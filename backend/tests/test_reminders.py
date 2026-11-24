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
    assert reminder["notification_delivery_status"] == "not_created"

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
    assert body["lifecycle_history"][-1]["action"] == "care.reminder.completed"
    assert body["lifecycle_history"][-1]["actor_caregiver_id"] == session["caregiver"]["id"]
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

        delivered_view = client.get(
            f"/api/v1/reminders/{session['person']['id']}",
            headers=headers,
        )
        assert delivered_view.status_code == 200, delivered_view.text
        assert delivered_view.json()[0]["notification_delivery_status"] == "in_app_notification_created"
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
    assert body["lifecycle_history"][-1]["action"] == "care.reminder.snoozed"
    assert body["lifecycle_history"][-1]["actor_caregiver_id"] == session["caregiver"]["id"]
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
        assert snoozed.json()["lifecycle_history"][-1]["action"] == "care.reminder.snoozed"
        completed = client.patch(
            f"/api/v1/reminders/{reminder_ids[1]}/complete",
            headers=headers,
            json={},
        )
        assert completed.status_code == 200, completed.text
        assert completed.json()["lifecycle_history"][-1]["action"] == "care.reminder.completed"
        cancelled = client.patch(
            f"/api/v1/reminders/{reminder_ids[2]}/cancel",
            headers=headers,
        )
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["lifecycle_history"][-1]["action"] == "care.reminder.cancelled"

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


def test_browser_push_is_never_advertised_under_in_app_only_policy():
    client = TestClient(app)
    session = create_session(client, "In-app only notification caregiver")
    headers = {"Authorization": f"Bearer {session['access_token']}"}

    response = client.get("/api/v1/notifications/push/public-key", headers=headers)

    assert response.status_code == 200, response.text
    assert response.json() == {"available": False, "public_key": None, "reason": "in_app_only"}


def test_timezone_preference_change_releases_stale_quiet_hours_deferral():
    from app.models.notifications import CareNotification, CareNotificationPreference
    from app.models.care import Caregiver
    from app.models.caregiver_intelligence import CaregiverProfile
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

    reminder_id = reminder.id
    changed = client.put(
        "/api/v1/notifications/preferences",
        headers=headers,
        json={"timezone_name": "Asia/Tokyo"},
    )
    assert changed.status_code == 200, changed.text
    db = TestSessionLocal()
    try:
        stored = db.query(CareReminder).filter_by(id=reminder_id).one()
        assert stored.notification_deferred_until is None
        caregiver = db.query(Caregiver).filter_by(
            id=session["caregiver"]["id"], person_id=session["person"]["id"],
        ).one()
        assert caregiver.timezone == "Asia/Tokyo"
        assert caregiver.timezone_configured is True
        profile = db.query(CaregiverProfile).filter_by(
            person_id=session["person"]["id"],
            session_caregiver_id=session["caregiver"]["id"],
        ).first()
        if profile is not None:
            assert profile.timezone == "Asia/Tokyo"
        # 02:00 in Tokyo is outside the configured 09:00-18:00 quiet window.
        assert deliver_due_reminders(db=db, now=now) == 1
        assert db.query(CareNotification).filter(
            CareNotification.source_event_id == reminder_id,
            CareNotification.category == "reminder_due",
        ).count() == 1
    finally:
        db.close()


def test_browser_push_registration_is_rejected_for_every_care_record():
    client = TestClient(app)
    first = create_session(client, "First in-app-only caregiver")
    second = create_session(client, "Second in-app-only caregiver")
    payload = {
        "endpoint": "https://fcm.googleapis.com/fcm/send/solenos-test-subscription",
        "keys": {"p256dh": "test-public-key", "auth": "test-auth-secret"},
    }

    for session in (first, second):
        response = client.post(
            "/api/v1/notifications/push/subscriptions",
            headers={"Authorization": f"Bearer {session['access_token']}"},
            json=payload,
        )
        assert response.status_code == 410, response.text


def test_browser_push_worker_revokes_legacy_subscriptions_and_cancels_deliveries():
    import hashlib
    from datetime import datetime, timezone
    from app.models.notifications import (
        CareNotification,
        CarePushDelivery,
        CarePushSubscription,
        DeliveryFailureEvent,
        NotificationDeliveryAttempt,
    )
    import app.services.reminder_delivery as delivery_service

    client = TestClient(app)
    session = create_session(client, "Legacy browser alert caregiver")
    person_id = session["person"]["id"]
    caregiver_id = session["caregiver"]["id"]
    endpoint = "https://fcm.googleapis.com/fcm/send/legacy-solenos-subscription"
    now = datetime.now(timezone.utc)
    db = TestSessionLocal()
    try:
        notification = CareNotification(
            person_id=person_id,
            caregiver_id=caregiver_id,
            title="Care reminder",
            body="A follow-up is due.",
            category="reminder_due",
            status="active",
        )
        subscription = CarePushSubscription(
            person_id=person_id,
            caregiver_id=caregiver_id,
            endpoint_hash=hashlib.sha256(endpoint.encode("utf-8")).hexdigest(),
            encrypted_subscription="legacy-encrypted-subscription",
        )
        db.add_all([notification, subscription])
        db.flush()
        delivery = CarePushDelivery(
            notification_id=notification.id,
            subscription_id=subscription.id,
            status="pending",
            attempt_count=0,
            next_attempt_at=now,
        )
        db.add(delivery)
        db.commit()
        delivery_id = delivery.id

        assert delivery_service.deliver_pending_web_pushes(db=db, now=now) == 0

        db.refresh(subscription)
        db.refresh(delivery)
        assert subscription.revoked_at is not None and subscription.revoked_at.replace(tzinfo=timezone.utc) == now
        assert delivery.status == "cancelled"
        assert delivery.last_error_code == "in_app_only_policy"
        assert delivery.attempt_count == 0
        assert delivery.provider_accepted_at is None
        assert db.query(NotificationDeliveryAttempt).filter_by(notification_id=notification.id).count() == 0
        assert db.query(DeliveryFailureEvent).filter_by(delivery_id=delivery_id).count() == 0
        assert db.query(CareNotification).filter_by(id=notification.id, status="active").count() == 1
    finally:
        db.close()


def test_reminder_assignment_requires_active_member_and_preserves_actor_history():
    from app.models.privacy_delivery import AuditLogEvent
    from app.models.reminder import CareReminder

    client = TestClient(app)
    primary = create_session(client, "Primary caregiver")
    primary_headers = {"Authorization": f"Bearer {primary['access_token']}"}
    invite = client.post(
        "/api/v1/caregiver-access/invitations",
        headers=primary_headers,
        json={"email": "assigned-caregiver@example.test", "expires_in_days": 7},
    )
    assert invite.status_code == 201, invite.text
    accepted = client.post(
        "/api/v1/caregiver-access/invitations/accept",
        json={
            "token": invite.json()["invitation_token"],
            "email": "assigned-caregiver@example.test",
            "password": "assigned-caregiver-password-456",
            "name": "Assigned caregiver",
        },
    )
    assert accepted.status_code == 201, accepted.text
    invited = accepted.json()
    invited_headers = {"Authorization": f"Bearer {invited['access_token']}"}

    due_at = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    created = client.post(
        "/api/v1/reminders",
        headers=primary_headers,
        json={
            "person_id": primary["person"]["id"],
            "title": "Confirm transport",
            "due_at": due_at,
            "assigned_to_caregiver_id": invited["caregiver_id"],
        },
    )
    assert created.status_code == 201, created.text
    reminder = created.json()
    assert reminder["created_by_caregiver_id"] == primary["caregiver"]["id"]
    assert reminder["assigned_to_caregiver_id"] == invited["caregiver_id"]
    assert reminder["version"] == 1

    reassigned = client.patch(
        f"/api/v1/reminders/{reminder['id']}/assignment",
        headers=invited_headers,
        json={
            "assigned_to_caregiver_id": primary["caregiver"]["id"],
            "expected_version": reminder["version"],
        },
    )
    assert reassigned.status_code == 200, reassigned.text
    assert reassigned.json()["assigned_to_caregiver_id"] == primary["caregiver"]["id"]
    assert reassigned.json()["version"] == 2

    stale_replay = client.patch(
        f"/api/v1/reminders/{reminder['id']}/assignment",
        headers=primary_headers,
        json={
            "assigned_to_caregiver_id": invited["caregiver_id"],
            "expected_version": reminder["version"],
        },
    )
    assert stale_replay.status_code == 409, stale_replay.text

    db = TestSessionLocal()
    try:
        stored = db.query(CareReminder).filter_by(id=reminder["id"]).one()
        assert stored.created_by_caregiver_id == primary["caregiver"]["id"]
        assert stored.assigned_to_caregiver_id == primary["caregiver"]["id"]
        assert stored.version == 2
        events = db.query(AuditLogEvent).filter(
            AuditLogEvent.target_type == "care_reminder",
            AuditLogEvent.target_id == reminder["id"],
        ).order_by(AuditLogEvent.occurred_at.asc()).all()
        assert len(events) == 2
        assert events[0].action == "care.reminder.assigned"
        assert events[0].actor_id == primary["caregiver"]["id"]
        assert events[0].record_metadata["assigned_to_caregiver_id"] == invited["caregiver_id"]
        assert events[1].action == "care.reminder.reassigned"
        assert events[1].actor_id == invited["caregiver_id"]
        assert events[1].record_metadata["previous_assignee_caregiver_id"] == invited["caregiver_id"]
        assert events[1].record_metadata["assigned_to_caregiver_id"] == primary["caregiver"]["id"]
    finally:
        db.close()


def test_reminder_assignment_rejects_inactive_or_foreign_caregiver():
    client = TestClient(app)
    primary = create_session(client, "Reminder owner")
    foreign = create_session(client, "Unrelated caregiver")
    headers = {"Authorization": f"Bearer {primary['access_token']}"}
    due_at = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    created = client.post(
        "/api/v1/reminders",
        headers=headers,
        json={
            "person_id": primary["person"]["id"],
            "title": "Follow up",
            "due_at": due_at,
        },
    )
    assert created.status_code == 201, created.text
    rejected = client.patch(
        f"/api/v1/reminders/{created.json()['id']}/assignment",
        headers=headers,
        json={
            "assigned_to_caregiver_id": foreign["caregiver"]["id"],
            "expected_version": created.json()["version"],
        },
    )
    assert rejected.status_code == 403, rejected.text


def test_due_reminder_notification_targets_current_assignee_not_creator():
    from app.models.notifications import CareNotification
    from app.services.reminder_delivery import deliver_due_reminders

    client = TestClient(app)
    primary = create_session(client, "Reminder creator")
    primary_headers = {"Authorization": f"Bearer {primary['access_token']}"}
    invite = client.post(
        "/api/v1/caregiver-access/invitations",
        headers=primary_headers,
        json={"email": "reminder-assignee@example.test"},
    )
    assert invite.status_code == 201, invite.text
    accepted = client.post(
        "/api/v1/caregiver-access/invitations/accept",
        json={
            "token": invite.json()["invitation_token"],
            "email": "reminder-assignee@example.test",
            "password": "reminder-assignee-password-456",
            "name": "Reminder assignee",
        },
    )
    assert accepted.status_code == 201, accepted.text

    due_at = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    created = client.post(
        "/api/v1/reminders",
        headers=primary_headers,
        json={
            "person_id": primary["person"]["id"],
            "title": "Assigned care follow-up",
            "due_at": due_at,
            "assigned_to_caregiver_id": accepted.json()["caregiver_id"],
        },
    )
    assert created.status_code == 201, created.text

    db = TestSessionLocal()
    try:
        assert deliver_due_reminders(db=db) == 1
        notification = db.query(CareNotification).filter_by(
            source_event_id=created.json()["id"],
            category="reminder_due",
        ).one()
        assert notification.caregiver_id == accepted.json()["caregiver_id"]
        assert notification.caregiver_id != primary["caregiver"]["id"]
    finally:
        db.close()


def test_due_reminder_is_not_disclosed_to_revoked_assignee_or_silently_reassigned():
    from app.models.notifications import CareNotification
    from app.models.reminder import CareReminder
    from app.services.reminder_delivery import deliver_due_reminders

    client = TestClient(app)
    primary = create_session(client, "Reminder owner")
    headers = {"Authorization": f"Bearer {primary['access_token']}"}
    invitation = client.post(
        "/api/v1/caregiver-access/invitations",
        headers=headers,
        json={"email": "revoked-reminder-assignee@example.test"},
    )
    assert invitation.status_code == 201, invitation.text
    accepted = client.post(
        "/api/v1/caregiver-access/invitations/accept",
        json={
            "token": invitation.json()["invitation_token"],
            "email": "revoked-reminder-assignee@example.test",
            "password": "revoked-reminder-password-456",
            "name": "Revoked assignee",
        },
    )
    assert accepted.status_code == 201, accepted.text
    assignee_id = accepted.json()["caregiver_id"]

    due_at = datetime(2000, 1, 1, tzinfo=timezone.utc).isoformat()
    created = client.post(
        "/api/v1/reminders",
        headers=headers,
        json={
            "person_id": primary["person"]["id"],
            "title": "Private assigned follow-up",
            "due_at": due_at,
            "assigned_to_caregiver_id": assignee_id,
        },
    )
    assert created.status_code == 201, created.text

    revoked = client.delete(f"/api/v1/caregiver-access/members/{assignee_id}", headers=headers)
    assert revoked.status_code == 200, revoked.text

    db = TestSessionLocal()
    try:
        assert deliver_due_reminders(db=db, now=datetime.now(timezone.utc)) == 0
        reminder = db.query(CareReminder).filter_by(id=created.json()["id"]).one()
        assert reminder.status == "open"
        assert reminder.assigned_to_caregiver_id == assignee_id
        assert reminder.notification_deferred_until is not None
        assert db.query(CareNotification).filter_by(
            source_event_id=reminder.id,
            category="reminder_due",
        ).count() == 0
    finally:
        db.close()


def test_reminders_cannot_be_assigned_to_caregivers_without_completion_permission():
    client = TestClient(app)
    primary = create_session(client, "Primary caregiver")
    primary_headers = {"Authorization": f"Bearer {primary['access_token']}"}
    person_id = primary["person"]["id"]
    primary_id = primary["caregiver"]["id"]
    invite_email = "reminder-permission@example.test"

    invitation = client.post(
        "/api/v1/caregiver-access/invitations",
        headers=primary_headers,
        json={"email": invite_email, "expires_in_days": 7},
    )
    assert invitation.status_code == 201, invitation.text
    accepted = client.post(
        "/api/v1/caregiver-access/invitations/accept",
        json={
            "token": invitation.json()["invitation_token"],
            "email": invite_email,
            "password": "reminder-permission-password-456",
            "name": "Second caregiver",
        },
    )
    assert accepted.status_code == 201, accepted.text
    second_id = accepted.json()["caregiver_id"]

    permissions = [
        "view_lcr",
        "add_evidence",
        "manage_responsibilities",
        "manage_reminders",
        "manage_handoffs",
        "share_summaries",
        "share_observations",
        "view_documents",
        "view_communications",
    ]
    updated = client.patch(
        f"/api/v1/caregiver-access/members/{second_id}/permissions",
        headers=primary_headers,
        json={"permissions": permissions},
    )
    assert updated.status_code == 200, updated.text

    due_at = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    create_for_ineligible = client.post(
        "/api/v1/reminders",
        headers=primary_headers,
        json={
            "person_id": person_id,
            "title": "Do not assign un-actionable responsibility",
            "due_at": due_at,
            "assigned_to_caregiver_id": second_id,
        },
    )
    assert create_for_ineligible.status_code == 403, create_for_ineligible.text

    created_for_primary = client.post(
        "/api/v1/reminders",
        headers=primary_headers,
        json={
            "person_id": person_id,
            "title": "Keep responsibility with an eligible caregiver",
            "due_at": due_at,
            "assigned_to_caregiver_id": primary_id,
        },
    )
    assert created_for_primary.status_code == 201, created_for_primary.text
    reminder_id = created_for_primary.json()["id"]
    reassignment = client.patch(
        f"/api/v1/reminders/{reminder_id}/assignment",
        headers=primary_headers,
        json={"assigned_to_caregiver_id": second_id, "expected_version": 1},
    )
    assert reassignment.status_code == 403, reassignment.text

    restored = client.patch(
        f"/api/v1/caregiver-access/members/{second_id}/permissions",
        headers=primary_headers,
        json={"permissions": permissions + ["complete_assigned_reminders"]},
    )
    assert restored.status_code == 200, restored.text
    eligible_assignment = client.patch(
        f"/api/v1/reminders/{reminder_id}/assignment",
        headers=primary_headers,
        json={"assigned_to_caregiver_id": second_id, "expected_version": 1},
    )
    assert eligible_assignment.status_code == 200, eligible_assignment.text
    assert eligible_assignment.json()["assigned_to_caregiver_id"] == second_id

    # Permission removal must not strand open work with a caregiver who can no
    # longer complete it. Reassign the open reminder first, then removal is safe.
    blocked_removal = client.patch(
        f"/api/v1/caregiver-access/members/{second_id}/permissions",
        headers=primary_headers,
        json={"permissions": permissions},
    )
    assert blocked_removal.status_code == 409, blocked_removal.text

    reassigned_back = client.patch(
        f"/api/v1/reminders/{reminder_id}/assignment",
        headers=primary_headers,
        json={"assigned_to_caregiver_id": primary_id, "expected_version": 2},
    )
    assert reassigned_back.status_code == 200, reassigned_back.text
    assert reassigned_back.json()["assigned_to_caregiver_id"] == primary_id

    safe_removal = client.patch(
        f"/api/v1/caregiver-access/members/{second_id}/permissions",
        headers=primary_headers,
        json={"permissions": permissions},
    )
    assert safe_removal.status_code == 200, safe_removal.text

    assignable = client.get("/api/v1/caregiver-access/assignable-members", headers=primary_headers)
    assert assignable.status_code == 200, assignable.text
    eligibility = {item["caregiver_id"]: item["can_complete_assigned_reminders"] for item in assignable.json()}
    assert eligibility[second_id] is False
    assert eligibility[primary_id] is True


def test_timezone_preference_change_releases_assigned_reminder_deferral_for_recipient():
    from app.models.notifications import CareNotification, CareNotificationPreference
    from app.models.reminder import CareReminder
    from app.services.reminder_delivery import deliver_due_reminders

    client = TestClient(app)
    primary = create_session(client, "Reminder creator")
    primary_headers = {"Authorization": f"Bearer {primary['access_token']}"}
    invitation = client.post(
        "/api/v1/caregiver-access/invitations",
        headers=primary_headers,
        json={"email": "timezone-assignee@example.test"},
    )
    assert invitation.status_code == 201, invitation.text
    accepted = client.post(
        "/api/v1/caregiver-access/invitations/accept",
        json={
            "token": invitation.json()["invitation_token"],
            "email": "timezone-assignee@example.test",
            "password": "timezone-assignee-password-456",
            "name": "Assigned caregiver",
        },
    )
    assert accepted.status_code == 201, accepted.text
    assignee = accepted.json()
    assignee_headers = {"Authorization": f"Bearer {assignee['access_token']}"}

    due_at = datetime(2026, 1, 15, 16, 0, tzinfo=timezone.utc).isoformat()
    created = client.post(
        "/api/v1/reminders",
        headers=primary_headers,
        json={
            "person_id": primary["person"]["id"],
            "title": "Assigned caregiver timezone follow-up",
            "due_at": due_at,
            "assigned_to_caregiver_id": assignee["caregiver_id"],
        },
    )
    assert created.status_code == 201, created.text
    reminder_id = created.json()["id"]
    now = datetime(2026, 1, 15, 17, 0, tzinfo=timezone.utc)

    db = TestSessionLocal()
    try:
        db.add(CareNotificationPreference(
            person_id=primary["person"]["id"],
            caregiver_id=assignee["caregiver_id"],
            reminders_enabled=True,
            quiet_hours_enabled=True,
            quiet_hours_start="09:00",
            quiet_hours_end="18:00",
            timezone_name="America/New_York",
        ))
        db.commit()
        assert deliver_due_reminders(db=db, now=now) == 0
        stored = db.query(CareReminder).filter_by(id=reminder_id).one()
        assert stored.notification_deferred_until is not None
        assert db.query(CareNotification).filter_by(
            source_event_id=reminder_id,
            caregiver_id=assignee["caregiver_id"],
            category="reminder_due",
        ).count() == 0
    finally:
        db.close()

    changed = client.put(
        "/api/v1/notifications/preferences",
        headers=assignee_headers,
        json={"timezone_name": "Asia/Tokyo"},
    )
    assert changed.status_code == 200, changed.text

    db = TestSessionLocal()
    try:
        stored = db.query(CareReminder).filter_by(id=reminder_id).one()
        assert stored.notification_deferred_until is None
        assert deliver_due_reminders(db=db, now=now) == 1
        notification = db.query(CareNotification).filter_by(
            source_event_id=reminder_id,
            caregiver_id=assignee["caregiver_id"],
            category="reminder_due",
        ).one()
        assert notification.person_id == primary["person"]["id"]
    finally:
        db.close()
