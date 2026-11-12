from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.main import app
from app.models.notifications import CareNotification
from app.models.privacy_delivery import NotificationReadReceipt


engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_db():
    db = TestSession()
    try:
        yield db
    finally:
        db.close()


def setup_client(monkeypatch):
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = override_db
    import app.main as main_module
    monkeypatch.setattr(main_module, "SessionLocal", TestSession)
    client = TestClient(app)
    first = client.post("/api/v1/sessions/anonymous", json={"caregiver_name": "Family caregiver"})
    second = client.post("/api/v1/sessions/anonymous", json={"caregiver_name": "Another caregiver"})
    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    return client, first.json(), second.json()


def teardown():
    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=engine)


def test_notification_preferences_are_saved_and_scoped(monkeypatch):
    client, first, second = setup_client(monkeypatch)
    try:
        first_headers = {"Authorization": f"Bearer {first['access_token']}"}
        second_headers = {"Authorization": f"Bearer {second['access_token']}"}

        initial = client.get("/api/v1/notifications/preferences", headers=first_headers)
        assert initial.status_code == 200, initial.text
        assert initial.json()["quiet_hours_enabled"] is False
        assert initial.json()["reminders_enabled"] is True

        saved = client.put(
            "/api/v1/notifications/preferences",
            headers=first_headers,
            json={"quiet_hours_enabled": True, "reminders_enabled": False},
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["quiet_hours_enabled"] is True
        assert saved.json()["reminders_enabled"] is False

        other = client.get("/api/v1/notifications/preferences", headers=second_headers)
        assert other.status_code == 200, other.text
        assert other.json()["quiet_hours_enabled"] is False
        assert other.json()["reminders_enabled"] is True
    finally:
        teardown()


def test_notifications_feed_unread_count_and_read_are_scoped(monkeypatch):
    client, first, second = setup_client(monkeypatch)
    try:
        first_headers = {"Authorization": f"Bearer {first['access_token']}"}
        second_headers = {"Authorization": f"Bearer {second['access_token']}"}
        person_id = first["person"]["id"]
        caregiver_id = first["caregiver"]["id"]

        db = TestSession()
        try:
            item = CareNotification(
                person_id=person_id,
                caregiver_id=caregiver_id,
                title="Care update",
                body="A saved care update is available.",
                category="care_update",
                priority="normal",
            )
            resolved_item = CareNotification(
                person_id=person_id,
                caregiver_id=caregiver_id,
                title="Previously resolved care change",
                body="This change was superseded by newer saved information.",
                category="care_change",
                priority="normal",
                source_event_id="resolved-source",
                status="resolved",
                resolved_at=datetime.now(timezone.utc),
                resolution_reason="source_superseded",
            )
            db.add_all([item, resolved_item])
            db.commit()
            db.refresh(item)
            item_id = item.id
        finally:
            db.close()

        feed = client.get("/api/v1/notifications?limit=100&offset=0", headers=first_headers)
        assert feed.status_code == 200, feed.text
        assert len(feed.json()["items"]) == 2
        active_item = next(row for row in feed.json()["items"] if row["id"] == item_id)
        resolved_feed_item = next(row for row in feed.json()["items"] if row["category"] == "care_change")
        assert active_item["is_read"] is False
        assert active_item["status"] == "active"
        assert resolved_feed_item["status"] == "resolved"
        assert resolved_feed_item["resolution_reason"] == "source_superseded"

        unread = client.get("/api/v1/notifications/unread-count", headers=first_headers)
        assert unread.status_code == 200, unread.text
        assert unread.json()["unread_count"] == 1

        other_feed = client.get("/api/v1/notifications?limit=100&offset=0", headers=second_headers)
        assert other_feed.status_code == 200, other_feed.text
        assert other_feed.json()["items"] == []

        denied_read = client.patch(f"/api/v1/notifications/{item_id}/read", headers=second_headers, json={})
        assert denied_read.status_code == 404, denied_read.text

        marked = client.patch(f"/api/v1/notifications/{item_id}/read", headers=first_headers, json={})
        assert marked.status_code == 200, marked.text
        assert marked.json()["is_read"] is True
        db = TestSession()
        try:
            assert db.query(NotificationReadReceipt).filter_by(
                notification_id=item_id,
                reader_id=caregiver_id,
            ).count() == 1
        finally:
            db.close()
    finally:
        teardown()
