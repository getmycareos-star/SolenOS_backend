import os
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import get_db
from app.main import app


def _incident_test_database():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.begin() as connection:
        connection.execute(text("""
            CREATE TABLE privacy_incidents (
                id VARCHAR(36) PRIMARY KEY,
                incident_status TEXT,
                severity TEXT NOT NULL,
                detected_at DATETIME NOT NULL,
                closed_at DATETIME,
                incident_type TEXT,
                owner_id TEXT,
                actor_id TEXT,
                metadata JSON,
                created_at DATETIME,
                updated_at DATETIME
            )
        """))
        connection.execute(text("""
            CREATE TABLE privacy_incident_events (
                id VARCHAR(36) PRIMARY KEY,
                incident_id VARCHAR(36) NOT NULL,
                event_type TEXT NOT NULL,
                occurred_at DATETIME NOT NULL,
                actor_id TEXT,
                notes_redacted TEXT,
                created_at DATETIME,
                updated_at DATETIME
            )
        """))
    return engine


def test_incident_operator_api_requires_configured_high_entropy_token(monkeypatch):
    monkeypatch.delenv("SOLENOS_INCIDENT_ADMIN_TOKEN", raising=False)
    client = TestClient(app)
    response = client.get("/api/v1/security/incidents")
    assert response.status_code == 503
    assert response.json()["detail"] == "Incident operator access is not configured"

    monkeypatch.setenv("SOLENOS_INCIDENT_ADMIN_TOKEN", "x" * 40)
    response = client.get(
        "/api/v1/security/incidents",
        headers={"X-SolenOS-Incident-Admin-Token": "wrong"},
    )
    assert response.status_code == 401


def test_incident_operator_api_creates_and_appends_auditable_timeline(monkeypatch):
    token = "test-only-incident-operator-token-0123456789"
    monkeypatch.setenv("SOLENOS_INCIDENT_ADMIN_TOKEN", token)
    engine = _incident_test_database()
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    def override_get_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    headers = {"X-SolenOS-Incident-Admin-Token": token}
    try:
        created = client.post(
            "/api/v1/security/incidents",
            headers=headers,
            json={
                "severity": "SEV-2",
                "incident_type": "suspected_unauthorized_access",
                "detected_at": datetime.now(timezone.utc).isoformat(),
                "owner_id": "incident-commander",
                "description_redacted": "Synthetic test incident; no caregiver data.",
                "affected_systems": ["backend-api"],
            },
        )
        assert created.status_code == 201, created.text
        incident_id = created.json()["id"]

        appended = client.post(
            f"/api/v1/security/incidents/{incident_id}/events",
            headers=headers,
            json={
                "event_type": "containment_started",
                "notes_redacted": "Synthetic session revoked for exercise.",
            },
        )
        assert appended.status_code == 201, appended.text

        status = client.patch(
            f"/api/v1/security/incidents/{incident_id}/status",
            headers=headers,
            json={
                "incident_status": "contained",
                "notes_redacted": "Synthetic exercise confirms test token revoked.",
            },
        )
        assert status.status_code == 200, status.text

        listed = client.get("/api/v1/security/incidents", headers=headers)
        assert listed.status_code == 200
        assert listed.json()["incidents"][0]["id"] == incident_id
        assert listed.json()["incidents"][0]["incident_status"] == "contained"

        events = client.get(
            f"/api/v1/security/incidents/{incident_id}/events",
            headers=headers,
        )
        assert events.status_code == 200
        assert [item["event_type"] for item in events.json()["events"]] == [
            "incident_opened",
            "containment_started",
            "status_changed",
        ]
    finally:
        app.dependency_overrides.pop(get_db, None)
        engine.dispose()
