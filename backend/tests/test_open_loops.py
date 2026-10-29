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


def session(client, name):
    response = client.post(
        "/api/v1/sessions/anonymous",
        json={"name": name, "caregiver_name": "Caregiver"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    return body, {"Authorization": f"Bearer {body['access_token']}"}


def create_loop(client, headers, loop_type="unresolved_concern", **overrides):
    payload = {
        "loop_type": loop_type,
        "title": "Follow up on the reported change",
        "description": "Caregiver reported a change; cause remains unknown.",
        "evidence": [{"kind": "caregiver_report", "text": "Caregiver report"}],
        **overrides,
    }
    response = client.post("/api/v1/open-loops", json=payload, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def test_open_loop_persists_and_lists_only_within_server_issued_session():
    client = TestClient(app)
    first, first_headers = session(client, "First care recipient")
    second, second_headers = session(client, "Second care recipient")
    created = create_loop(client, first_headers)
    assert created["person_id"] == first["person"]["id"]
    assert created["caregiver_id"] == first["caregiver"]["id"]
    assert created["status"] == "open"

    listed = client.get("/api/v1/open-loops", headers=first_headers)
    assert listed.status_code == 200, listed.text
    assert [item["id"] for item in listed.json()["items"]] == [created["id"]]

    isolated = client.get("/api/v1/open-loops", headers=second_headers)
    assert isolated.status_code == 200, isolated.text
    assert isolated.json()["items"] == []
    denied = client.get(f"/api/v1/open-loops/{created['id']}", headers=second_headers)
    assert denied.status_code == 404, denied.text


def test_deadline_requires_timezone_and_source():
    client = TestClient(app)
    _, headers = session(client, "Care recipient")
    no_timezone = create_payload = {
        "loop_type": "follow_up",
        "title": "Follow up",
        "description": "A recorded follow-up",
        "deadline_at": "2026-11-01T09:00:00",
        "deadline_source": "explicit_user",
    }
    response = client.post("/api/v1/open-loops", json=no_timezone, headers=headers)
    assert response.status_code == 422, response.text

    no_source = {
        "loop_type": "follow_up",
        "title": "Follow up",
        "description": "A recorded follow-up",
        "deadline_at": "2026-11-01T09:00:00Z",
    }
    response = client.post("/api/v1/open-loops", json=no_source, headers=headers)
    assert response.status_code == 422, response.text


def test_action_loop_cannot_close_until_outcome_is_verified():
    client = TestClient(app)
    _, headers = session(client, "Care recipient")
    loop = create_loop(client, headers, "pending_action")
    closure = client.post(
        f"/api/v1/open-loops/{loop['id']}/close",
        headers=headers,
        json={"text": "Action was marked complete", "evidence": [{"kind": "action_note"}]},
    )
    assert closure.status_code == 409, closure.text

    reported = client.post(
        f"/api/v1/open-loops/{loop['id']}/outcomes",
        headers=headers,
        json={"text": "Caregiver reports intended outcome", "evidence": [{"kind": "caregiver_report"}]},
    )
    assert reported.status_code == 200, reported.text
    assert reported.json()["outcome_status"] == "outcome_reported"

    verified = client.post(
        f"/api/v1/open-loops/{loop['id']}/verify-outcome",
        headers=headers,
        json={
            "text": "Outcome checked against the stated verification rule",
            "evidence": [{"kind": "supporting_record", "reference": "recorded-by-caregiver"}],
            "verification_rule": "The recorded intended outcome is corroborated by the linked source record.",
        },
    )
    assert verified.status_code == 200, verified.text
    assert verified.json()["outcome_status"] == "outcome_verified"

    closure = client.post(
        f"/api/v1/open-loops/{loop['id']}/close",
        headers=headers,
        json={"text": "Closure reviewed", "evidence": [{"kind": "closure_review"}]},
    )
    assert closure.status_code == 200, closure.text
    assert closure.json()["verification"] == "verified"
    assert closure.json()["loop"]["status"] == "closed"


def test_professional_input_requires_source_evidence():
    client = TestClient(app)
    _, headers = session(client, "Care recipient")
    loop = create_loop(client, headers, "professional_input")
    response = client.post(
        f"/api/v1/open-loops/{loop['id']}/responses",
        headers=headers,
        json={"text": "A professional responded", "evidence": []},
    )
    assert response.status_code == 422, response.text


def test_dependency_cycle_is_rejected_and_prerequisite_blocks_dependent():
    client = TestClient(app)
    _, headers = session(client, "Care recipient")
    prerequisite = create_loop(client, headers, "pending_action", title="Prerequisite")
    dependent = create_loop(client, headers, "pending_decision", title="Dependent")

    edge = client.post(
        f"/api/v1/open-loops/{dependent['id']}/dependencies",
        headers=headers,
        json={"prerequisite_id": prerequisite["id"], "mandatory": True},
    )
    assert edge.status_code == 200, edge.text
    assert edge.json()["blocked"] is True

    cycle = client.post(
        f"/api/v1/open-loops/{prerequisite['id']}/dependencies",
        headers=headers,
        json={"prerequisite_id": dependent["id"], "mandatory": True},
    )
    assert cycle.status_code == 409, cycle.text


def test_invalid_loop_type_is_rejected():
    client = TestClient(app)
    _, headers = session(client, "Care recipient")
    response = client.post(
        "/api/v1/open-loops",
        headers=headers,
        json={"loop_type": "invented", "title": "No", "description": "Invalid type"},
    )
    assert response.status_code == 422, response.text
