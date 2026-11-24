"""End-to-end API contracts for caregiver preferences and timezone authority."""

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.main import app
from app.models.care import Caregiver
from app.models.notifications import CareNotificationPreference
from app.models.privacy_delivery import AuditLogEvent


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


def setup_client():
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = override_db
    return TestClient(app)


def teardown_client():
    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=engine)


def create_caregiver(client, name):
    response = client.post(
        "/api/v1/sessions/anonymous",
        json={"caregiver_name": name},
    )
    assert response.status_code == 201, response.text
    data = response.json()
    return data, {"Authorization": f"Bearer {data['access_token']}"}


def test_caregiver_preferences_persist_and_are_scoped_to_authenticated_caregiver():
    client = setup_client()
    try:
        first, first_headers = create_caregiver(client, "Mary Jane Williams")
        second, second_headers = create_caregiver(client, "John Smith")

        saved = client.patch(
            "/api/v1/caregiver/preferences",
            headers=first_headers,
            json={
                "preferred_name": "Mary",
                "timezone": "America/New_York",
                "locale": "en-US",
                "date_format": "MM/DD/YYYY",
                "measurement_system": "us_customary",
                "communication_style": "concise",
            },
        )
        assert saved.status_code == 200, saved.text
        assert saved.json() == {
            "preferred_name": "Mary",
            "timezone": "America/New_York",
            "timezone_configured": True,
            "locale": "en-US",
            "date_format": "MM/DD/YYYY",
            "measurement_system": "us_customary",
            "communication_style": "concise",
        }

        first_read = client.get("/api/v1/caregiver/preferences", headers=first_headers)
        assert first_read.status_code == 200, first_read.text
        assert first_read.json() == saved.json()

        db = TestSession()
        try:
            audit = db.query(AuditLogEvent).filter_by(
                person_id=first["person"]["id"],
                caregiver_id=first["caregiver"]["id"],
                action="caregiver.preferences.update",
                target_type="caregiver_preferences",
                target_id=first["caregiver"]["id"],
            ).one()
            assert "timezone" in audit.record_metadata["updated_fields"]
        finally:
            db.close()

        second_read = client.get("/api/v1/caregiver/preferences", headers=second_headers)
        assert second_read.status_code == 200, second_read.text
        assert second_read.json()["preferred_name"] is None
        assert second_read.json()["timezone_configured"] is False
        assert second_read.json()["communication_style"] is None

        # Request-body identity claims cannot redirect a profile update to another account.
        spoofed = client.patch(
            "/api/v1/caregiver/preferences",
            headers=second_headers,
            json={"preferred_name": "John", "caregiver_id": first["caregiver"]["id"]},
        )
        assert spoofed.status_code == 200, spoofed.text
        second_after = client.get("/api/v1/caregiver/preferences", headers=second_headers)
        assert second_after.json()["preferred_name"] == "John"
        first_after = client.get("/api/v1/caregiver/preferences", headers=first_headers)
        assert first_after.json()["preferred_name"] == "Mary"
        assert first_after.json()["timezone"] == "America/New_York"
    finally:
        teardown_client()


def test_notification_timezone_never_establishes_or_overwrites_companion_timezone():
    client = setup_client()
    try:
        session, headers = create_caregiver(client, "Caregiver")
        initial = client.get("/api/v1/caregiver/preferences", headers=headers)
        assert initial.status_code == 200, initial.text
        assert initial.json()["timezone_configured"] is False

        notification_update = client.put(
            "/api/v1/notifications/preferences",
            headers=headers,
            json={"timezone_name": "America/Chicago"},
        )
        assert notification_update.status_code == 200, notification_update.text
        assert notification_update.json()["timezone_name"] == "America/Chicago"

        caregiver_after_notification_update = client.get(
            "/api/v1/caregiver/preferences", headers=headers,
        )
        assert caregiver_after_notification_update.status_code == 200
        assert caregiver_after_notification_update.json()["timezone_configured"] is False
        assert caregiver_after_notification_update.json()["timezone"] is None

        caregiver_update = client.patch(
            "/api/v1/caregiver/preferences",
            headers=headers,
            json={"timezone": "America/Los_Angeles"},
        )
        assert caregiver_update.status_code == 200, caregiver_update.text
        assert caregiver_update.json()["timezone"] == "America/Los_Angeles"

        notification_after_caregiver_update = client.get(
            "/api/v1/notifications/preferences", headers=headers,
        )
        assert notification_after_caregiver_update.status_code == 200
        assert notification_after_caregiver_update.json()["timezone_name"] == "America/Chicago"

        db = TestSession()
        try:
            caregiver = db.query(Caregiver).filter_by(
                id=session["caregiver"]["id"],
                person_id=session["person"]["id"],
            ).one()
            notification_preference = db.query(CareNotificationPreference).filter_by(
                caregiver_id=session["caregiver"]["id"],
                person_id=session["person"]["id"],
            ).one()
            assert caregiver.timezone == "America/Los_Angeles"
            assert caregiver.timezone_configured is True
            assert notification_preference.timezone_name == "America/Chicago"
        finally:
            db.close()
    finally:
        teardown_client()


def test_caregiver_timezone_can_be_cleared_without_claiming_utc_is_configured():
    client = setup_client()
    try:
        _, headers = create_caregiver(client, "Caregiver")
        configured = client.patch(
            "/api/v1/caregiver/preferences",
            headers=headers,
            json={"timezone": "America/Denver"},
        )
        assert configured.status_code == 200, configured.text
        cleared = client.patch(
            "/api/v1/caregiver/preferences",
            headers=headers,
            json={"timezone": None},
        )
        assert cleared.status_code == 200, cleared.text
        assert cleared.json()["timezone"] is None
        assert cleared.json()["timezone_configured"] is False

        reread = client.get("/api/v1/caregiver/preferences", headers=headers)
        assert reread.status_code == 200
        assert reread.json()["timezone"] is None
        assert reread.json()["timezone_configured"] is False
    finally:
        teardown_client()


def test_invalid_timezone_and_invalid_preference_enums_are_rejected_without_persisting():
    client = setup_client()
    try:
        _, headers = create_caregiver(client, "Caregiver")
        invalid_zone = client.patch(
            "/api/v1/caregiver/preferences",
            headers=headers,
            json={"timezone": "Mars/Olympus_Mons"},
        )
        assert invalid_zone.status_code == 422, invalid_zone.text

        invalid_style = client.patch(
            "/api/v1/caregiver/preferences",
            headers=headers,
            json={"communication_style": "sarcastic"},
        )
        assert invalid_style.status_code == 422, invalid_style.text

        current = client.get("/api/v1/caregiver/preferences", headers=headers)
        assert current.status_code == 200
        assert current.json()["timezone_configured"] is False
        assert current.json()["communication_style"] is None
    finally:
        teardown_client()



def test_caregiver_preferences_require_a_valid_bearer_session():
    client = setup_client()
    try:
        for headers in ({}, {"Authorization": "Bearer invalid-token"}):
            read = client.get("/api/v1/caregiver/preferences", headers=headers)
            assert read.status_code == 401, read.text
            update = client.patch(
                "/api/v1/caregiver/preferences",
                headers=headers,
                json={"preferred_name": "Unverified"},
            )
            assert update.status_code == 401, update.text
    finally:
        teardown_client()



def test_explicitly_selected_utc_is_authoritative_not_confused_with_database_default():
    client = setup_client()
    try:
        _, headers = create_caregiver(client, "Caregiver")
        initial = client.get("/api/v1/caregiver/preferences", headers=headers)
        assert initial.status_code == 200
        assert initial.json()["timezone"] is None
        assert initial.json()["timezone_configured"] is False

        saved = client.patch(
            "/api/v1/caregiver/preferences",
            headers=headers,
            json={"timezone": "UTC"},
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["timezone"] == "UTC"
        assert saved.json()["timezone_configured"] is True

        reread = client.get("/api/v1/caregiver/preferences", headers=headers)
        assert reread.status_code == 200
        assert reread.json()["timezone"] == "UTC"
        assert reread.json()["timezone_configured"] is True
    finally:
        teardown_client()
