from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.api.auth as auth
import app.core.database as database
import app.main as main
from app.core.database import Base, get_db
from app.models.auth import AuthAccount
from app.models.care import Caregiver, Person


def test_registration_requires_enrollment_and_persists_account_and_care_context(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(database, "SessionLocal", TestingSessionLocal)
    monkeypatch.setattr(main, "SessionLocal", TestingSessionLocal)
    secret = "test-only-enrollment-auth-secret-at-least-32-bytes"
    monkeypatch.setattr(main.settings, "secret_key", secret)
    monkeypatch.setattr(auth.settings, "secret_key", secret)
    monkeypatch.setenv("ACCOUNT_ENROLLMENT_KEY", "test-enrollment-key-with-sufficient-length")

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    main.app.dependency_overrides[get_db] = override_get_db
    client = TestClient(main.app)
    try:
        payload = {
            "email": "new-caregiver@example.com",
            "password": "a-secure-test-password-123",
            "name": "Test Caregiver",
            "person_name": "Test Care Recipient",
            "relationship": "Family caregiver",
        }

        denied = client.post(
            "/api/v1/auth/register",
            json={**payload, "enrollment_key": "wrong-enrollment-key"},
        )
        assert denied.status_code == 403, denied.text

        db = TestingSessionLocal()
        try:
            assert db.query(AuthAccount).count() == 0
            assert db.query(Caregiver).count() == 0
            assert db.query(Person).count() == 0
        finally:
            db.close()

        created = client.post(
            "/api/v1/auth/register",
            json={**payload, "enrollment_key": "test-enrollment-key-with-sufficient-length"},
        )
        assert created.status_code == 201, created.text
        result = created.json()
        assert result["token_type"] == "bearer"
        assert result["account"]["role"] == "caregiver"
        assert result["account"]["caregiver_id"]
        assert result["account"]["person_id"]

        headers = {"Authorization": f"Bearer {result['access_token']}"}
        contexts = client.get("/api/v1/auth/care-contexts", headers=headers)
        assert contexts.status_code == 200, contexts.text
        assert {row["person_id"] for row in contexts.json()} == {result["account"]["person_id"]}

        links = client.get(
            f"/api/v1/auth/my-caregiver-links?person_id={result['account']['person_id']}",
            headers=headers,
        )
        assert links.status_code == 200, links.text
        assert links.json()[0]["account_id"] == result["account"]["id"]
        assert links.json()[0]["caregiver_id"] == result["account"]["caregiver_id"]

        db = TestingSessionLocal()
        try:
            account = db.query(AuthAccount).filter_by(id=result["account"]["id"]).one()
            caregiver = db.query(Caregiver).filter_by(id=result["account"]["caregiver_id"]).one()
            person = db.query(Person).filter_by(id=result["account"]["person_id"]).one()
            assert account.email == payload["email"]
            assert caregiver.person_id == person.id
        finally:
            db.close()
    finally:
        main.app.dependency_overrides.clear()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()
