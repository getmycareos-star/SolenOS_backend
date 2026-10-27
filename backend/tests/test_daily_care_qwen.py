import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.main as main_module
import app.api.daily_care_intelligence as daily_qwen
import app.api.collaboration as collaboration
from app.core.database import Base, get_db, SessionLocal as DefaultSessionLocal
from app.main import app
from app.models.care import CareEvent
from app.models.care_continuity import CareAssertion

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


def create_session(client, name):
    response = client.post(
        "/api/v1/sessions/anonymous",
        json={"name": name, "caregiver_name": "Family caregiver"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def add_event(session, title, description, when):
    db = TestSessionLocal()
    event = CareEvent(
        person_id=session["person"]["id"],
        event_type="observation",
        status="recorded",
        occurred_at=datetime.fromisoformat(when.replace("Z", "+00:00")),
        title=title,
        description=description,
        created_by_caregiver_id=session["caregiver"]["id"],
        time_provenance="caregiver_reported",
        tags=["daily_capture"],
        evidence_ids=[],
    )
    db.add(event)
    db.commit()
    event_id = event.id
    db.close()
    return event_id


def test_daily_care_history_is_paginated_and_scoped_to_authenticated_person():
    client = TestClient(app)
    first = create_session(client, "First recipient")
    second = create_session(client, "Second recipient")
    add_event(first, "Earlier sleep", "Caregiver reported poor sleep.", "2026-10-01T08:00:00Z")
    add_event(first, "Today's meal", "Caregiver reported eating breakfast.", "2026-10-27T08:00:00Z")
    add_event(second, "Foreign record", "Must not appear in first record.", "2026-10-27T09:00:00Z")

    auth = {"Authorization": f"Bearer {first['access_token']}"}
    page1 = client.get("/api/v1/daily-care/intelligence/history?person_id=" + first["person"]["id"] + "&limit=1&offset=0", headers=auth)
    assert page1.status_code == 200, page1.text
    body1 = page1.json()
    assert body1["page"]["total_matching"] == 2
    assert body1["page"]["has_more"] is True
    assert "Foreign record" not in json.dumps(body1)

    page2 = client.get("/api/v1/daily-care/intelligence/history?person_id=" + first["person"]["id"] + "&limit=1&offset=1", headers=auth)
    assert page2.status_code == 200, page2.text
    assert page2.json()["page"]["returned"] == 1
    assert page2.json()["page"]["has_more"] is False


def test_qwen_answer_proposes_review_only_lcr_assertion_and_preserves_source(monkeypatch):
    client = TestClient(app)
    session = create_session(client, "Care recipient")
    event_id = add_event(
        session, "Sleep observation", "Caregiver reported poor sleep last night.",
        "2026-10-26T22:00:00Z",
    )
    auth = {"Authorization": f"Bearer {session['access_token']}"}

    def fake_qwen(*args, **kwargs):
        return json.dumps({
            "answer": f"The caregiver recorded poor sleep on 2026-10-26 [source: {event_id}].",
            "proposals": [{
                "title": "Sleep observation",
                "field": "sleep",
                "proposed_value": "Caregiver reported poor sleep last night.",
                "source_ids": [event_id],
                "uncertainty": "Caregiver report; no cause established.",
                "reason": "May help compare future sleep observations.",
            }],
        })

    monkeypatch.setattr(daily_qwen, "qwen_chat", fake_qwen)
    answer = client.post(
        "/api/v1/daily-care/intelligence/ask",
        headers=auth,
        json={"person_id": session["person"]["id"], "question": "What was recorded about sleep?", "prepare_lcr_proposals": True},
    )
    assert answer.status_code == 200, answer.text
    result = answer.json()
    assert result["status"] == "completed"
    assert result["written_to_lcr"] is False
    assert result["proposal_analysis_id"]
    assert result["proposals"][0]["source_ids"] == [event_id]
    assert result["history_scope"]["records_sent_to_model"] >= 1

    corrected = client.post(
        f"/api/v1/daily-care/intelligence/proposals/{result['proposal_analysis_id']}/0/review",
        headers=auth,
        json={"decision": "correct", "corrected_value": "Caregiver reported that sleep was poor last night."},
    )
    assert corrected.status_code == 200, corrected.text
    assert corrected.json()["written_to_lcr"] is False
    assert corrected.json()["proposal"]["proposed_value"] == "Caregiver reported that sleep was poor last night."
    assert corrected.json()["proposal"]["review_status"] == "corrected_pending_approval"

    approved = client.post(
        f"/api/v1/daily-care/intelligence/proposals/{result['proposal_analysis_id']}/0/review",
        headers=auth,
        json={"decision": "accept", "reason": "Reviewed against the original daily entry."},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["written_to_lcr"] is True
    repeated = client.post(
        f"/api/v1/daily-care/intelligence/proposals/{result['proposal_analysis_id']}/0/review",
        headers=auth,
        json={"decision": "accept"},
    )
    assert repeated.status_code == 409, repeated.text

    db = TestSessionLocal()
    try:
        assertion = db.query(CareAssertion).filter_by(person_id=session["person"]["id"]).one()
        assert assertion.source_event_id == event_id
        assert assertion.asserted_value == "Caregiver reported that sleep was poor last night."
        assert assertion.assertion_status == "caregiver_reviewed"
        assert assertion.created_by_caregiver_id == session["caregiver"]["id"]
        from app.models.capture_analysis import CaptureAnalysis
        analysis = db.query(CaptureAnalysis).filter_by(id=result["proposal_analysis_id"]).one()
        assert analysis.review_history[0]["original_proposal"]["proposed_value"] == "Caregiver reported poor sleep last night."
        assert analysis.review_history[0]["reviewed_by_caregiver_id"] == session["caregiver"]["id"]
        # Original care event remains unchanged; acceptance adds an assertion.
        event = db.query(CareEvent).filter_by(id=event_id).one()
        assert event.description == "Caregiver reported poor sleep last night."
    finally:
        db.close()

    captured = {}
    def fake_companion(base, model, messages, options=None):
        captured["messages"] = messages
        return "The caregiver-reviewed record says sleep was poor last night."

    monkeypatch.setattr(collaboration, "_ollama_url", lambda: ("http://qwen.test", "qwen-test"))
    monkeypatch.setattr(collaboration, "_ollama_chat", fake_companion)
    # Keep this integration test independent of whether the CI runner has a
    # configured local Qwen endpoint; the dedicated ask path is tested separately.
    monkeypatch.setattr(collaboration, "qwen_chat", lambda *args, **kwargs: fake_companion("", "", args[0] if args else [], kwargs.get("options")))
    companion = client.post(
        "/api/v1/conversation/message",
        headers=auth,
        json={"person_id": session["person"]["id"], "text": "What is recorded about sleep?"},
    )
    assert companion.status_code == 200, companion.text
    companion_body = companion.json()
    assert approved.json()["proposal"]["review_status"] == "accepted_by_caregiver"
    assert companion_body["source_assertion_ids"]
    serialized_messages = json.dumps(captured["messages"], ensure_ascii=False)
    assert "Caregiver reported that sleep was poor last night." in serialized_messages
    assert approved.json()["proposal"]["source_ids"][0] in serialized_messages


def test_daily_care_proposal_cannot_be_reviewed_by_another_caregiver_session(monkeypatch):
    client = TestClient(app)
    first = create_session(client, "First recipient")
    second = create_session(client, "Second recipient")
    event_id = add_event(first, "Sleep", "Caregiver reported interrupted sleep.", "2026-10-27T07:00:00Z")
    monkeypatch.setattr(daily_qwen, "qwen_chat", lambda *args, **kwargs: json.dumps({
        "answer": "The record mentions interrupted sleep.",
        "proposals": [{
            "title": "Sleep",
            "field": "sleep",
            "proposed_value": "Caregiver reported interrupted sleep.",
            "source_ids": [event_id],
            "uncertainty": "Reported, not independently verified.",
            "reason": "Longitudinal continuity.",
        }],
    }))
    first_auth = {"Authorization": f"Bearer {first['access_token']}"}
    response = client.post(
        "/api/v1/daily-care/intelligence/ask",
        headers=first_auth,
        json={"person_id": first["person"]["id"], "question": "Summarize sleep", "prepare_lcr_proposals": True},
    )
    assert response.status_code == 200, response.text
    analysis_id = response.json()["proposal_analysis_id"]
    foreign = client.post(
        f"/api/v1/daily-care/intelligence/proposals/{analysis_id}/0/review",
        headers={"Authorization": f"Bearer {second['access_token']}"},
        json={"decision": "accept"},
    )
    assert foreign.status_code == 404, foreign.text
    db = TestSessionLocal()
    try:
        assert db.query(CareAssertion).filter_by(person_id=second["person"]["id"]).count() == 0
    finally:
        db.close()
