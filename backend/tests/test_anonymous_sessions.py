import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.main as main_module
from app.core.database import Base, get_db, SessionLocal as DefaultSessionLocal
from app.main import app

pytestmark = pytest.mark.session_security

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


def create_session(client, person_name):
    response = client.post(
        "/api/v1/sessions/anonymous",
        json={"name": person_name, "caregiver_name": "Anonymous caregiver"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["person"]["id"]
    assert body["caregiver"]["id"]
    assert body["access_token"]
    return body


def test_anonymous_session_bootstrap_creates_scoped_record_without_email():
    client = TestClient(app)
    created = create_session(client, "Care recipient")
    assert created["token_type"] == "Bearer"

    from app.models.care import Caregiver, Person
    from app.models.anonymous_session import AnonymousSession
    import hashlib

    db = TestSessionLocal()
    try:
        caregiver = db.query(Caregiver).filter_by(id=created["caregiver"]["id"]).one()
        session = db.query(AnonymousSession).filter_by(
            token_hash=hashlib.sha256(created["access_token"].encode()).hexdigest()
        ).one()
        person = db.query(Person).filter_by(id=created["person"]["id"]).one()
        assert caregiver.email is None
        assert caregiver.person_id == person.id
        assert session.person_id == person.id
        assert session.caregiver_id == caregiver.id
        assert created["access_token"] not in session.token_hash
    finally:
        db.close()


def test_care_data_requires_server_issued_bearer_token():
    client = TestClient(app)
    created = create_session(client, "Care recipient")
    response = client.get(f"/api/v1/evidence/{created['person']['id']}")
    assert response.status_code == 401, response.text


def test_session_cannot_read_another_person_evidence():
    client = TestClient(app)
    first = create_session(client, "First person")
    second = create_session(client, "Second person")
    response = client.get(
        f"/api/v1/evidence/{first['person']['id']}",
        headers={"Authorization": f"Bearer {second['access_token']}"},
    )
    assert response.status_code == 403, response.text


def test_session_cannot_spoof_another_caregiver_attribution():
    client = TestClient(app)
    first = create_session(client, "First person")
    second = create_session(client, "Second person")
    response = client.post(
        "/api/v1/daily-care/entries",
        headers={"Authorization": f"Bearer {first['access_token']}"},
        json={
            "person_id": first["person"]["id"],
            "caregiver_id": second["caregiver"]["id"],
            "mood": "Calm",
        },
    )
    assert response.status_code == 403, response.text


def test_situation_capture_uses_server_bound_person_and_caregiver():
    client = TestClient(app)
    created = create_session(client, "Care recipient")
    response = client.post(
        "/api/situation",
        headers={"Authorization": f"Bearer {created['access_token']}"},
        json={
            "raw_input": "Caregiver-reported observation",
            "caregiver_id": "client-generated-not-an-identity",
            "care_session_id": "client-generated-session",
        },
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["person_id"] == created["person"]["id"]
    assert result["care_key"] == "client-generated-not-an-identity"

    from app.models.care import CareEvent, Evidence
    db = TestSessionLocal()
    try:
        event = db.query(CareEvent).filter_by(id=result["care_event_id"]).one()
        evidence = db.query(Evidence).filter_by(id=result["evidence_id"]).one()
        assert event.person_id == created["person"]["id"]
        assert event.created_by_caregiver_id == created["caregiver"]["id"]
        assert evidence.uploaded_by_caregiver_id == created["caregiver"]["id"]
    finally:
        db.close()


def test_refresh_rotates_token_without_changing_record_scope():
    client = TestClient(app)
    created = create_session(client, "Care recipient")
    refreshed_response = client.post(
        "/api/v1/sessions/refresh",
        headers={"Authorization": f"Bearer {created['access_token']}"},
    )
    assert refreshed_response.status_code == 200, refreshed_response.text
    refreshed = refreshed_response.json()
    assert refreshed["person_id"] == created["person"]["id"]
    assert refreshed["caregiver_id"] == created["caregiver"]["id"]
    assert refreshed["access_token"] != created["access_token"]

    old_token_response = client.get(
        f"/api/v1/evidence/{created['person']['id']}",
        headers={"Authorization": f"Bearer {created['access_token']}"},
    )
    assert old_token_response.status_code == 401, old_token_response.text

    new_token_response = client.get(
        f"/api/v1/evidence/{created['person']['id']}",
        headers={"Authorization": f"Bearer {refreshed['access_token']}"},
    )
    assert new_token_response.status_code == 200, new_token_response.text


def test_situation_hydration_returns_only_persisted_records_for_session_scope():
    client = TestClient(app)
    created = create_session(client, "Care recipient")
    token_headers = {"Authorization": f"Bearer {created['access_token']}"}

    write = client.post(
        "/api/situation",
        headers=token_headers,
        json={
            "raw_input": "Caregiver reports that breakfast was left mostly uneaten.",
            "caregiver_id": "client-durable-care-key",
            "care_session_id": "interaction-session",
        },
    )
    assert write.status_code == 200, write.text

    read = client.get(
        "/api/situation?caregiver_id=client-durable-care-key&care_session_id=interaction-session&offer_return_invite=0",
        headers=token_headers,
    )
    assert read.status_code == 200, read.text
    data = read.json()
    assert data["care_recipient_id"] == created["person"]["id"]
    assert data["care_key"] == "client-durable-care-key"
    assert data["has_context_root"] is True
    assert data["total_events"] == 1
    assert len(data["active_care_situation"]["observations"]) == 1
    observation = data["active_care_situation"]["observations"][0]
    assert observation["raw_text"] == "Caregiver reports that breakfast was left mostly uneaten."
    assert observation["human_fact"] == observation["raw_text"]
    assert observation["event_ids"] == [write.json()["care_event_id"]]
    assert data["active_care_situation_turn"]["what_seems_happening"] is None


def test_situation_hydration_requires_session_and_ignores_spoofed_person_query():
    client = TestClient(app)
    first = create_session(client, "First person")
    second = create_session(client, "Second person")

    no_token = client.get("/api/situation")
    assert no_token.status_code == 401, no_token.text

    response = client.get(
        f"/api/situation?person_id={first['person']['id']}&caregiver_id={second['caregiver']['id']}",
        headers={"Authorization": f"Bearer {second['access_token']}"},
    )
    assert response.status_code == 403, response.text


def test_care_recipient_name_update_is_scoped_to_session():
    client = TestClient(app)
    first = create_session(client, "First person")
    second = create_session(client, "Second person")

    denied = client.patch(
        "/api/situation",
        headers={"Authorization": f"Bearer {first['access_token']}"},
        json={
            "person_id": second["person"]["id"],
            "caregiver_id": second["caregiver"]["id"],
            "display_name": "Not allowed",
        },
    )
    assert denied.status_code == 403, denied.text

    allowed = client.patch(
        "/api/situation",
        headers={"Authorization": f"Bearer {first['access_token']}"},
        json={
            "person_id": first["person"]["id"],
            "caregiver_id": first["caregiver"]["id"],
            "display_name": "Preferred label",
        },
    )
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["care_recipient_display_name"] == "Preferred label"

    from app.models.care import Person
    db = TestSessionLocal()
    try:
        assert db.query(Person).filter_by(id=first["person"]["id"]).one().name == "Preferred label"
        assert db.query(Person).filter_by(id=second["person"]["id"]).one().name == "Second person"
    finally:
        db.close()


def test_situation_hydration_never_returns_another_session_records():
    client = TestClient(app)
    first = create_session(client, "First person")
    second = create_session(client, "Second person")
    write = client.post(
        "/api/situation",
        headers={"Authorization": f"Bearer {first['access_token']}"},
        json={
            "raw_input": "Private observation for first person only.",
            "caregiver_id": "first-client-key",
        },
    )
    assert write.status_code == 200, write.text

    read = client.get(
        "/api/situation?caregiver_id=first-client-key",
        headers={"Authorization": f"Bearer {second['access_token']}"},
    )
    assert read.status_code == 200, read.text
    data = read.json()
    assert data["care_recipient_id"] == second["person"]["id"]
    assert data["total_events"] == 0
    assert data["active_care_situation"] is None
    assert data["active_care_situation_turn"] is None
    assert "Private observation for first person only." not in str(data)
