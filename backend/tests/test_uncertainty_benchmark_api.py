"""API/database integration contracts for the uncertainty benchmark.

These tests use a real SQLAlchemy schema and FastAPI request path against isolated SQLite.
They do not claim PostgreSQL production parity; PostgreSQL migration/E2E checks remain separate.
"""
import hashlib
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.main import app
from app.models.care import Caregiver, Person
from app.models.anonymous_session import AnonymousSession


@pytest.fixture
def uncertainty_api_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestingSessionLocal() as db:
            for suffix in ("a", "b"):
                person = Person(id=f"benchmark-person-{suffix}", name=f"Benchmark Person {suffix}")
                caregiver = Caregiver(
                    id=f"benchmark-caregiver-{suffix}",
                    name=f"Benchmark Caregiver {suffix}",
                    email=f"benchmark-{suffix}@example.invalid",
                    relationship="Family",
                    person_id=person.id,
                )
                db.add_all([person, caregiver])
            db.commit()
        yield TestingSessionLocal
    finally:
        app.dependency_overrides.clear()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def _client_for(TestingSessionLocal, suffix):
    token = f"benchmark-token-{suffix}"
    with TestingSessionLocal() as db:
        db.add(
            AnonymousSession(
                person_id=f"benchmark-person-{suffix}",
                caregiver_id=f"benchmark-caregiver-{suffix}",
                token_hash=hashlib.sha256(token.encode("utf-8")).hexdigest(),
                expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            )
        )
        db.commit()
    client = TestClient(app)
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


@pytest.mark.session_security
def test_uncertainty_api_denies_cross_person_gap_reads(uncertainty_api_db):
    client_a = _client_for(uncertainty_api_db, "a")
    response = client_a.get("/api/v1/uncertainty/gaps/benchmark-person-b")
    assert response.status_code == 403
    assert "not authorised" in response.json()["detail"].lower()


@pytest.mark.session_security
def test_unknown_assessment_persists_and_projects_as_unknown(uncertainty_api_db):
    client = _client_for(uncertainty_api_db, "a")
    created = client.post(
        "/api/v1/uncertainty/assessments",
        json={
            "person_id": "benchmark-person-a",
            "subject_type": "medication",
            "subject_id": "medication-1",
            "field": "current_status",
            "epistemic_state": "unknown",
            "gap_reason": "not_documented",
            "known_value": None,
        },
    )
    assert created.status_code == 200, created.text
    created_data = created.json()
    assert created_data["epistemic_state"] == "unknown"
    assert created_data.get("confidence") is None

    # A second HTTP request uses a fresh dependency-managed DB session.
    listed = client.get("/api/v1/uncertainty/assessments/benchmark-person-a")
    assert listed.status_code == 200, listed.text
    matching = [
        item for item in listed.json()
        if item.get("subject_id") == "medication-1" and item.get("field") == "current_status"
    ]
    assert len(matching) == 1
    assert matching[0]["epistemic_state"] == "unknown"
    assert matching[0].get("confidence") is None

    view = client.get(
        "/api/v1/uncertainty/view/benchmark-person-a/medication/medication-1"
    )
    assert view.status_code == 200, view.text
    assert view.json()["epistemic_state"] == "unknown"
    assert view.json().get("known_value") is None
