import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text, inspect
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


@pytest.fixture
def client():
    Base.metadata.create_all(bind=engine)
    with engine.begin() as connection:
        connection.execute(text("""
            CREATE TABLE IF NOT EXISTS care_facts (
                id VARCHAR(36) PRIMARY KEY,
                person_id VARCHAR(255) NOT NULL,
                fact_type VARCHAR(100),
                fact_value TEXT,
                epistemic_status VARCHAR(40),
                effective_at DATETIME,
                created_at DATETIME,
                updated_at DATETIME,
                review_notes TEXT
            )
        """))
    app.dependency_overrides[get_db] = override_get_db
    main_module.SessionLocal = TestSessionLocal
    with TestClient(app) as test_client:
        created = test_client.post(
            "/api/v1/sessions/anonymous",
            json={"name": "Care recipient", "caregiver_name": "Family caregiver"},
        )
        assert created.status_code == 201, created.text
        yield test_client, created.json()
    app.dependency_overrides.clear()
    main_module.SessionLocal = DefaultSessionLocal
    Base.metadata.drop_all(bind=engine)


def test_draft_requires_session(client):
    test_client, _ = client
    response = test_client.post("/api/v1/handoff-drafts/prepare", json={})
    assert response.status_code == 401, response.text


def test_draft_is_read_only_and_only_includes_records_for_session_person(client):
    test_client, session = client
    token = session["access_token"]
    person_id = session["person"]["id"]
    with engine.begin() as connection:
        connection.execute(
            text("""INSERT INTO care_facts
                (id, person_id, subject_type, fact_type, fact_value, epistemic_status, created_at)
                VALUES (:id, :person_id, :subject_type, :fact_type, :fact_value, :status, :created_at)"""),
            {
                "id": "owned-fact",
                "person_id": person_id,
                "subject_type": "care_recipient",
                "fact_type": "caregiver_observation",
                "fact_value": "Breakfast was mostly uneaten",
                "status": "reported",
                "created_at": "2026-10-26 08:00:00",
            },
        )
        connection.execute(
            text("""INSERT INTO care_facts
                (id, person_id, fact_type, fact_value, epistemic_status, created_at)
                VALUES (:id, :person_id, :fact_type, :fact_value, :status, :created_at)"""),
            {
                "id": "other-person-fact",
                "person_id": "another-person",
                "subject_type": "care_recipient",
                "fact_type": "private",
                "fact_value": "MUST NOT LEAK",
                "status": "reported",
                "created_at": "2026-10-26 08:00:00",
            },
        )
    response = test_client.post(
        "/api/v1/handoff-drafts/prepare",
        headers={"Authorization": f"Bearer {token}"},
        json={"purpose": "family_transition", "window_days": 90},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["read_only"] is True
    assert body["creates_handoff"] is False
    assert body["updates_lcr"] is False
    assert any(item["source_id"] == "owned-fact" for item in body["source_references"])
    assert all(item["source_id"] != "other-person-fact" for item in body["source_references"])
    assert "MUST NOT LEAK" not in response.text
    assert body["source_statuses"]["community"]["status"] == "not_authorized"
    assert body["source_statuses"]["documents"]["status"] == "unavailable"
    with engine.connect() as connection:
        saved = connection.execute(
            text("SELECT fact_value FROM care_facts WHERE id = 'owned-fact'")
        ).scalar_one()
    assert saved == "Breakfast was mostly uneaten"


@pytest.mark.parametrize("window_days", [0, -1, 91, 999])
def test_draft_rejects_unbounded_time_window(client, window_days):
    test_client, session = client
    response = test_client.post(
        "/api/v1/handoff-drafts/prepare",
        headers={"Authorization": f"Bearer {session['access_token']}"},
        json={"window_days": window_days},
    )
    assert response.status_code == 422, response.text
