from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.core.database as database
import app.main as main
import app.api.auth as auth
from app.core.database import Base
from app.models.auth import AuthAccount
from app.models.care import Caregiver, CareEvent, Person


def test_authenticated_caregiver_is_isolated_to_linked_care_recipient(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    secret = "test-only-auth-isolation-secret-at-least-32-bytes"
    monkeypatch.setattr(main.settings, "secret_key", secret)
    monkeypatch.setattr(auth.settings, "secret_key", secret)
    monkeypatch.setattr(database, "SessionLocal", TestingSessionLocal)
    monkeypatch.setattr(main, "SessionLocal", TestingSessionLocal)
    main.app.dependency_overrides.clear()

    db = TestingSessionLocal()
    try:
        person_a = Person(id="isolation-person-a", name="Test Person A")
        person_b = Person(id="isolation-person-b", name="Test Person B")
        caregiver_a = Caregiver(
            id="isolation-caregiver-a",
            name="Test Caregiver A",
            email="caregiver-a@example.invalid",
            relationship="Family",
            person_id=person_a.id,
        )
        caregiver_b = Caregiver(
            id="isolation-caregiver-b",
            name="Test Caregiver B",
            email="caregiver-b@example.invalid",
            relationship="Family",
            person_id=person_b.id,
        )
        db.add_all([person_a, person_b, caregiver_a, caregiver_b])
        db.flush()
        account_a = AuthAccount(
            id="isolation-account-a",
            caregiver_id=caregiver_a.id,
            email=caregiver_a.email,
            password_hash="test-only",
            role="caregiver",
            is_active=True,
        )
        db.add(account_a)
        db.commit()
        db.refresh(account_a)
        token_a = auth.issue_token(account_a)
    finally:
        db.close()

    client = TestClient(main.app)
    headers = {"Authorization": f"Bearer {token_a}"}
    try:
        # Missing credentials must be rejected by the real ASGI middleware.
        no_token = client.get("/api/v1/persons/isolation-person-a")
        assert no_token.status_code == 401, no_token.text

        # A valid caregiver token must not read another recipient's record.
        cross_recipient = client.get(
            "/api/v1/persons/isolation-person-b",
            headers=headers,
        )
        assert cross_recipient.status_code == 403, cross_recipient.text

        # Authenticated requests to private endpoints may not omit recipient scope.
        unscoped_private = client.get("/api/v1/changes/detect", headers=headers)
        assert unscoped_private.status_code == 400, unscoped_private.text
        assert "Care recipient scope is required" in unscoped_private.json()["detail"]

        # The authenticated caregiver can write to their linked recipient.
        created = client.post(
            "/api/v1/daily-care/entries",
            headers=headers,
            json={
                "person_id": "isolation-person-a",
                "caregiver_id": "isolation-caregiver-a",
                "general_notes": "isolation read-back verification",
            },
        )
        assert created.status_code == 200, created.text
        event_id = created.json()["id"]

        # Verify actual database persistence and recipient-scoped read-back.
        db = TestingSessionLocal()
        try:
            persisted = db.query(CareEvent).filter_by(id=event_id).one()
            assert persisted.person_id == "isolation-person-a"
            assert persisted.created_by_caregiver_id == "isolation-caregiver-a"
        finally:
            db.close()

        read_back = client.get("/api/v1/events/isolation-person-a", headers=headers)
        assert read_back.status_code == 200, read_back.text
        assert any(
            event["id"] == event_id and event["person_id"] == "isolation-person-a"
            for event in read_back.json()
        )

        # Attempting to submit the other recipient/caregiver identity is denied.
        cross_write = client.post(
            "/api/v1/daily-care/entries",
            headers=headers,
            json={
                "person_id": "isolation-person-b",
                "caregiver_id": "isolation-caregiver-b",
                "general_notes": "must not be written",
            },
        )
        assert cross_write.status_code == 403, cross_write.text
    finally:
        main.app.dependency_overrides.clear()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()
