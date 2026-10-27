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
    assert body["caregiver"]["profile_id"]
    assert body["access_token"]
    return body


def test_anonymous_session_bootstrap_creates_scoped_record_without_email():
    client = TestClient(app)
    created = create_session(client, "Care recipient")
    assert created["token_type"] == "Bearer"

    from app.models.care import Caregiver, Person
    from app.models.caregiver_intelligence import CaregiverProfile
    from app.models.anonymous_session import AnonymousSession
    import hashlib

    db = TestSessionLocal()
    try:
        caregiver = db.query(Caregiver).filter_by(id=created["caregiver"]["id"]).one()
        session = db.query(AnonymousSession).filter_by(
            token_hash=hashlib.sha256(created["access_token"].encode()).hexdigest()
        ).one()
        person = db.query(Person).filter_by(id=created["person"]["id"]).one()
        profile = db.query(CaregiverProfile).filter_by(id=created["caregiver"]["profile_id"]).one()
        assert profile.person_id == person.id
        assert profile.session_caregiver_id == caregiver.id
        assert profile.email is None
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


def test_same_session_read_ignores_legacy_caregiver_query_but_keeps_person_scope():
    client = TestClient(app)
    created = create_session(client, "Care recipient")
    response = client.get(
        f"/api/v1/evidence/{created['person']['id']}?caregiver_id=legacy-client-local-key",
        headers={"Authorization": f"Bearer {created['access_token']}"},
    )
    assert response.status_code == 200, response.text
    assert response.json() == []


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
    written = write.json()
    assert written["care_key"] == "client-durable-care-key"
    assert written["care_recipient_id"] == created["person"]["id"]
    assert written["has_context_root"] is True
    assert written["total_events"] == 1
    assert len(written["active_care_situation"]["observations"]) == 1
    assert written["active_care_situation"]["observations"][0]["raw_text"] == (
        "Caregiver reports that breakfast was left mostly uneaten."
    )
    assert written["analysis_status"] in {"complete", "partial"}

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


def _accept_required_privacy_terms(client, created, *, improvement=False):
    headers = {"Authorization": f"Bearer {created['access_token']}"}
    response = client.post(
        "/api/v1/consent",
        headers=headers,
        json={
            "person_id": created["person"]["id"],
            "caregiver_id": created["caregiver"]["id"],
            "medical_disclaimer_acknowledged": True,
            "privacy_model_acknowledged": True,
            "multi_caregiver_acknowledged": True,
            "no_advertising_acknowledged": True,
            "data_improvement_consent": improvement,
            "accepted_terms_version": "2026-10",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["verified"] is True
    return headers


def test_optional_improvement_preference_can_be_toggled_without_required_acknowledgement():
    client = TestClient(app)
    created = create_session(client, "Independent improvement preference recipient")
    headers = {"Authorization": f"Bearer {created['access_token']}"}
    url = "/api/v1/consent"

    initial = client.get(url, params={"person_id": created["person"]["id"]}, headers=headers)
    assert initial.status_code == 200, initial.text
    assert initial.json()["required"] is True
    assert initial.json()["verified"] is False

    for enabled in (True, False, True, False):
        saved = client.post(
            url,
            headers=headers,
            json={
                "person_id": created["person"]["id"],
                "caregiver_id": created["caregiver"]["id"],
                "action": "update_data_improvement",
                "data_improvement_consent": enabled,
            },
        )
        assert saved.status_code == 200, saved.text
        body = saved.json()
        assert body["profile"]["data_improvement_consent"] is enabled
        # The optional choice must not silently accept the required terms.
        assert body["verified"] is False
        assert body["required"] is True

        reread = client.get(
            url, params={"person_id": created["person"]["id"]}, headers=headers
        )
        assert reread.status_code == 200, reread.text
        assert reread.json()["profile"]["data_improvement_consent"] is enabled
        assert reread.json()["required"] is True


def test_required_privacy_and_optional_improvement_choice_persist_per_session():
    client = TestClient(app)
    created = create_session(client, "Consent persistence recipient")
    headers = {"Authorization": f"Bearer {created['access_token']}"}

    initial = client.get(
        f"/api/v1/consent?person_id={created['person']['id']}",
        headers=headers,
    )
    assert initial.status_code == 200, initial.text
    assert initial.json()["verified"] is False
    assert initial.json()["required"] is True

    _accept_required_privacy_terms(client, created, improvement=False)
    saved_off = client.get(
        f"/api/v1/consent?person_id={created['person']['id']}",
        headers=headers,
    )
    assert saved_off.status_code == 200, saved_off.text
    assert saved_off.json()["verified"] is True
    assert saved_off.json()["profile"]["data_improvement_consent"] is False

    opted_in = client.post(
        "/api/v1/consent",
        headers=headers,
        json={
            "person_id": created["person"]["id"],
            "caregiver_id": created["caregiver"]["id"],
            "action": "update_data_improvement",
            "data_improvement_consent": True,
        },
    )
    assert opted_in.status_code == 200, opted_in.text
    assert opted_in.json()["profile"]["data_improvement_consent"] is True

    opted_out = client.post(
        "/api/v1/consent",
        headers=headers,
        json={
            "person_id": created["person"]["id"],
            "caregiver_id": created["caregiver"]["id"],
            "action": "update_data_improvement",
            "data_improvement_consent": False,
        },
    )
    assert opted_out.status_code == 200, opted_out.text
    reread = client.get(
        f"/api/v1/consent?person_id={created['person']['id']}",
        headers=headers,
    )
    assert reread.status_code == 200, reread.text
    assert reread.json()["profile"]["data_improvement_consent"] is False


def test_improvement_feedback_requires_session_and_explicit_saved_opt_in():
    client = TestClient(app)
    created = create_session(client, "Feedback consent recipient")
    query = {
        "person_id": created["person"]["id"],
        "caregiver_id": created["caregiver"]["id"],
        "category": "help_improve",
        "detail": "Deliberately submitted feedback for the opt-in route. Contact caregiver@example.com on 2026-05-31."
    }

    no_session = client.post("/api/v1/feedback", params=query)
    assert no_session.status_code == 401, no_session.text

    headers = {"Authorization": f"Bearer {created['access_token']}"}
    opt_out = client.post("/api/v1/feedback", params=query, headers=headers)
    assert opt_out.status_code == 403, opt_out.text

    _accept_required_privacy_terms(client, created, improvement=False)
    still_opted_out = client.post("/api/v1/feedback", params=query, headers=headers)
    assert still_opted_out.status_code == 403, still_opted_out.text

    enabled = client.post(
        "/api/v1/consent",
        headers=headers,
        json={
            "person_id": created["person"]["id"],
            "caregiver_id": created["caregiver"]["id"],
            "action": "update_data_improvement",
            "data_improvement_consent": True,
        },
    )
    assert enabled.status_code == 200, enabled.text

    recorded = client.post("/api/v1/feedback", params=query, headers=headers)
    assert recorded.status_code == 200, recorded.text
    assert recorded.json()["event_type"] == "feedback"
    assert recorded.json()["source_type"] == "feedback"

    from app.models.improvement_training_example import ImprovementTrainingExample
    db = TestSessionLocal()
    try:
        staged = db.query(ImprovementTrainingExample).filter_by(
            person_id=created["person"]["id"], caregiver_id=created["caregiver"]["id"]
        ).all()
        assert len(staged) == 1
        assert staged[0].status == "pending_review"
        assert "caregiver@example.com" not in staged[0].sample_text
        assert "2026-05-31" not in staged[0].sample_text
        assert "[redacted email]" in staged[0].sample_text
        assert "[redacted date]" in staged[0].sample_text
    finally:
        db.close()

    # Opting out purges staged improvement data while retaining personal memory.
    disabled = client.post(
        "/api/v1/consent",
        headers=headers,
        json={
            "person_id": created["person"]["id"],
            "caregiver_id": created["caregiver"]["id"],
            "action": "update_data_improvement",
            "data_improvement_consent": False,
        },
    )
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["profile"]["data_improvement_consent"] is False
    blocked = client.post("/api/v1/feedback", params=query, headers=headers)
    assert blocked.status_code == 403, blocked.text

    from app.models.memory import ReasoningMemory
    db = TestSessionLocal()
    try:
        assert db.query(ImprovementTrainingExample).filter_by(
            person_id=created["person"]["id"], caregiver_id=created["caregiver"]["id"]
        ).count() == 0
        assert db.query(ReasoningMemory).filter_by(
            person_id=created["person"]["id"], memory_type="feedback"
        ).count() == 1
    finally:
        db.close()
