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


def test_done_for_now_pauses_interaction_without_resolving_or_deleting_care_reality():
    client = TestClient(app)
    created = create_session(client, "Pause verification recipient")
    headers = {"Authorization": f"Bearer {created['access_token']}"}
    interaction_id = "interaction-pause-verification"

    write = client.post(
        "/api/situation",
        headers=headers,
        json={
            "raw_input": "Caregiver reports breakfast was mostly uneaten.",
            "caregiver_id": created["caregiver"]["id"],
            "care_session_id": interaction_id,
        },
    )
    assert write.status_code == 200, write.text

    before = client.get(
        f"/api/situation?caregiver_id={created['caregiver']['id']}&care_session_id={interaction_id}&offer_return_invite=0",
        headers=headers,
    )
    assert before.status_code == 200, before.text
    before_data = before.json()

    pause = client.post(
        "/api/situation",
        headers=headers,
        json={
            "action": "pause_active_care_situation",
            "caregiver_id": created["caregiver"]["id"],
            "care_session_id": interaction_id,
        },
    )
    assert pause.status_code == 200, pause.text
    pause_data = pause.json()
    assert pause_data["action"] == "pause_active_care_situation"
    assert pause_data["interaction_session_id"] == interaction_id
    assert pause_data["paused_at"]
    assert pause_data["care_reality_preserved"] is True

    after = client.get(
        f"/api/situation?caregiver_id={created['caregiver']['id']}&care_session_id={interaction_id}&offer_return_invite=0",
        headers=headers,
    )
    assert after.status_code == 200, after.text
    after_data = after.json()
    assert after_data["care_recipient_id"] == before_data["care_recipient_id"]
    assert after_data["total_events"] == before_data["total_events"] == 1
    assert after_data["active_care_situation"]["observations"] == before_data["active_care_situation"]["observations"]


def test_done_for_now_cannot_pause_another_session_scope():
    client = TestClient(app)
    first = create_session(client, "First pause recipient")
    second = create_session(client, "Second pause recipient")

    denied = client.post(
        "/api/situation",
        headers={"Authorization": f"Bearer {first['access_token']}"},
        json={
            "action": "pause_active_care_situation",
            "person_id": second["person"]["id"],
            "caregiver_id": second["caregiver"]["id"],
            "care_session_id": "interaction-spoofed-pause",
        },
    )
    assert denied.status_code == 403, denied.text


def test_care_continuity_routes_enforce_person_scope_and_reviewer_attribution():
    client = TestClient(app)
    first = create_session(client, "First person")
    second = create_session(client, "Second person")
    first_headers = {"Authorization": f"Bearer {first['access_token']}"}

    created = client.post(
        "/api/v1/care-continuity/assertions",
        headers=first_headers,
        json={
            "person_id": first["person"]["id"],
            "subject_type": "medication",
            "subject_id": "medication-1",
            "field": "dose",
            "assertion_kind": "current_state",
            "asserted_value": "5 mg",
            "source_label": "caregiver report",
            "evidence_ids": [],
            "review_after_days": 30,
            "created_by_caregiver_id": first["caregiver"]["id"],
        },
    )
    assert created.status_code == 201, created.text
    assertion_id = created.json()["id"]

    cross_record_read = client.get(
        f"/api/v1/care-continuity/assertions/{second['person']['id']}",
        headers=first_headers,
    )
    assert cross_record_read.status_code == 403, cross_record_read.text

    spoofed_reviewer = client.patch(
        f"/api/v1/care-continuity/assertions/{assertion_id}/status",
        headers=first_headers,
        json={
            "person_id": first["person"]["id"],
            "assertion_status": "accepted",
            "review_after_days": 30,
            "updated_by_caregiver_id": second["caregiver"]["id"],
        },
    )
    assert spoofed_reviewer.status_code == 403, spoofed_reviewer.text

    accepted = client.patch(
        f"/api/v1/care-continuity/assertions/{assertion_id}/status",
        headers=first_headers,
        json={
            "person_id": first["person"]["id"],
            "assertion_status": "accepted",
            "review_after_days": 30,
            "updated_by_caregiver_id": first["caregiver"]["id"],
        },
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["assertion_status"] == "accepted"


def test_care_continuity_contradiction_keeps_both_reports_and_links_sources():
    client = TestClient(app)
    created = create_session(client, "Care recipient")
    headers = {"Authorization": f"Bearer {created['access_token']}"}
    person_id = created["person"]["id"]
    for value in ("5 mg", "10 mg"):
        response = client.post(
            "/api/v1/care-continuity/assertions",
            headers=headers,
            json={
                "person_id": person_id,
                "subject_type": "medication",
                "subject_id": "medication-1",
                "field": "dose",
                "assertion_kind": "current_state",
                "asserted_value": value,
                "source_label": "caregiver report",
                "evidence_ids": [],
                "review_after_days": 30,
                "created_by_caregiver_id": created["caregiver"]["id"],
            },
        )
        assert response.status_code == 201, response.text

    reports = client.get(f"/api/v1/care-continuity/assertions/{person_id}", headers=headers)
    conflicts = client.get(f"/api/v1/care-continuity/contradictions/{person_id}", headers=headers)
    assert reports.status_code == 200, reports.text
    assert {item["asserted_value"] for item in reports.json()} == {"5 mg", "10 mg"}
    assert conflicts.status_code == 200, conflicts.text
    conflict = next(item for item in conflicts.json() if item["field"] == "dose")
    assert conflict["resolution_status"] == "unresolved"
    assert set(conflict["source_assertion_ids"]) == {item["id"] for item in reports.json()}

    for route in ("revisions", "freshness", "outcomes", "assertions", "contradictions"):
        response = client.get(f"/api/v1/care-continuity/{route}/{person_id}", headers=headers)
        assert response.status_code == 200, f"{route}: {response.text}"
    questions = client.post(
        f"/api/v1/care-continuity/next-questions/{person_id}", headers=headers, json={}
    )
    assert questions.status_code == 200, questions.text
    assert any(item["source_kind"] == "contradiction" for item in questions.json())



def test_care_continuity_audit_actors_default_to_authenticated_session():
    client = TestClient(app)
    created = create_session(client, "Actor attribution recipient")
    headers = {"Authorization": f"Bearer {created['access_token']}"}
    person_id = created["person"]["id"]
    caregiver_id = created["caregiver"]["id"]

    def report(value):
        return client.post(
            "/api/v1/care-continuity/assertions",
            headers=headers,
            json={
                "person_id": person_id,
                "subject_type": "medication",
                "subject_id": "medication-actor-test",
                "field": "dose",
                "assertion_kind": "current_state",
                "asserted_value": value,
                "source_label": "caregiver report",
                "evidence_ids": [],
                "review_after_days": 30,
                # Intentionally omit created_by_caregiver_id.
            },
        )

    first = report("5 mg")
    second = report("10 mg")
    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    assert first.json()["created_by_caregiver_id"] == caregiver_id
    assert second.json()["created_by_caregiver_id"] == caregiver_id

    assertion_id = first.json()["id"]
    reviewed = client.patch(
        f"/api/v1/care-continuity/assertions/{assertion_id}/status",
        headers=headers,
        json={
            "person_id": person_id,
            "assertion_status": "accepted",
            "review_after_days": 30,
            # Intentionally omit updated_by_caregiver_id.
        },
    )
    assert reviewed.status_code == 200, reviewed.text
    assert reviewed.json()["updated_by_caregiver_id"] == caregiver_id

    freshness = client.get(
        f"/api/v1/care-continuity/freshness/{person_id}",
        headers=headers,
    )
    assert freshness.status_code == 200, freshness.text
    freshness_by_id = {item["assertion_id"]: item for item in freshness.json()}
    assert freshness_by_id[first.json()["id"]]["status"] == "needs_confirmation"
    assert "competing report" in freshness_by_id[first.json()["id"]]["explanation"].lower()

    contradictions = client.get(
        f"/api/v1/care-continuity/contradictions/{person_id}",
        headers=headers,
    )
    assert contradictions.status_code == 200, contradictions.text
    conflict = next(
        item for item in contradictions.json()
        if item["subject_id"] == "medication-actor-test" and item["field"] == "dose"
    )

    questions = client.post(
        f"/api/v1/care-continuity/next-questions/{person_id}",
        headers=headers,
        json={},
    )
    assert questions.status_code == 200, questions.text
    conflict_question = next(
        item for item in questions.json() if item["source_kind"] == "contradiction"
    )
    outcome = client.post(
        "/api/v1/care-continuity/outcomes",
        headers=headers,
        json={
            "person_id": person_id,
            "action_id": conflict_question["persisted_question_id"],
            "action_type": "next_best_question",
            "outcome_status": "useful",
            # Intentionally omit created_by_caregiver_id.
        },
    )
    assert outcome.status_code == 201, outcome.text
    assert outcome.json()["created_by_caregiver_id"] == caregiver_id

    resolved = client.patch(
        f"/api/v1/care-continuity/contradictions/{conflict['id']}/resolution",
        headers=headers,
        json={
            "person_id": person_id,
            "resolution_status": "qualified",
            "resolution_notes": "Reports remain competing; no source independently confirms either value.",
            # Intentionally omit resolved_by_caregiver_id.
        },
    )
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["resolved_by_caregiver_id"] == caregiver_id

    # "Qualified" retains uncertainty: it must not turn either competing report
    # into a confirmed current state.
    freshness_after_qualification = client.get(
        f"/api/v1/care-continuity/freshness/{person_id}",
        headers=headers,
    )
    assert freshness_after_qualification.status_code == 200, freshness_after_qualification.text
    after_qualification = {
        item["assertion_id"]: item for item in freshness_after_qualification.json()
    }
    assert after_qualification[first.json()["id"]]["status"] == "needs_confirmation"
    assert "competing report" in after_qualification[first.json()["id"]]["explanation"].lower()


def test_owner_can_read_caregivers_events_and_evidence_but_other_session_cannot():
    """Guard the route-level identity handoff seen in Railway smoke logs."""
    client = TestClient(app)
    first = create_session(client, "Owner scope person")
    second = create_session(client, "Different scope person")
    first_headers = {"Authorization": f"Bearer {first['access_token']}"}
    second_headers = {"Authorization": f"Bearer {second['access_token']}"}
    person_id = first["person"]["id"]

    # All three routes take the care-recipient ID, not caregiver ID, in the path.
    caregivers = client.get(f"/api/v1/caregivers/{person_id}", headers=first_headers)
    assert caregivers.status_code == 200, caregivers.text
    assert any(item["id"] == first["caregiver"]["id"] for item in caregivers.json())

    events = client.get(f"/api/v1/events/{person_id}", headers=first_headers)
    assert events.status_code == 200, events.text
    evidence = client.get(f"/api/v1/evidence/{person_id}", headers=first_headers)
    assert evidence.status_code == 200, evidence.text

    for route in ("caregivers", "events", "evidence"):
        denied = client.get(f"/api/v1/{route}/{person_id}", headers=second_headers)
        assert denied.status_code == 403, f"{route}: expected cross-record denial, got {denied.status_code}: {denied.text}"



def test_owner_can_read_care_record_and_delta_runs_but_other_session_cannot():
    """Person-scoped integration routes must resolve identity before authorization."""
    client = TestClient(app)
    first = create_session(client, "Owner care record route")
    second = create_session(client, "Other care record route")
    first_headers = {"Authorization": f"Bearer {first['access_token']}"}
    second_headers = {"Authorization": f"Bearer {second['access_token']}"}
    person_id = first["person"]["id"]

    for path in (
        f"/api/v1/care-record/{person_id}",
        f"/api/v1/care-delta/runs/{person_id}",
        f"/api/v1/actions/{person_id}?status=active",
    ):
        allowed = client.get(path, headers=first_headers)
        assert allowed.status_code == 200, f"{path}: expected owner access, got {allowed.status_code}: {allowed.text}"

        denied = client.get(path, headers=second_headers)
        assert denied.status_code == 403, f"{path}: expected cross-record denial, got {denied.status_code}: {denied.text}"
