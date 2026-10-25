import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.main as main_module
import app.api.v1 as api_v1_module
from app.core.database import Base, get_db, SessionLocal as DefaultSessionLocal
from app.main import app
from app.models.capture_analysis import CaptureAnalysis

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


def test_daily_capture_persists_candidates_and_review_decision_without_writing_lcr(monkeypatch):
    client = TestClient(app)
    session = create_session(client, "Care recipient")
    auth = {"Authorization": f"Bearer {session['access_token']}"}
    source = "Caregiver report: Mum slept badly last night."
    candidates = {
        "observations": [{
            "subject": "sleep",
            "observation": "Mum slept badly last night.",
            "source_quote": "Mum slept badly last night.",
            "review_status": "pending_review",
            "source_event_id": "event-from-capture",
        }],
        "open_questions": [],
        "possible_safety_flags": [],
    }
    monkeypatch.setattr(api_v1_module, "extract_review_candidates", lambda *args, **kwargs: candidates)

    saved = client.post(
        "/api/v1/daily/log",
        headers=auth,
        json={
            "person_id": session["person"]["id"],
            "caregiver_id": session["caregiver"]["id"],
            "notes": source,
            "medications": [],
            "vitals": [],
            "scales": [],
        },
    )
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["qwen_extraction_status"] == "available"
    assert body["qwen_analysis_id"]
    assert body["qwen_written_to_lcr"] is False

    reviewed = client.post(
        f"/api/v1/capture/analyses/{body['qwen_analysis_id']}/review",
        headers=auth,
        json={
            "candidate_index": 0,
            "decision": "correct",
            "corrected_observation": "Mum slept poorly last night.",
        },
    )
    assert reviewed.status_code == 200, reviewed.text
    result = reviewed.json()
    assert result["candidate"]["observation"] == "Mum slept poorly last night."
    assert result["candidate"]["review_status"] == "corrected_by_caregiver"
    assert result["candidate"]["reviewed_by_caregiver_id"] == session["caregiver"]["id"]
    assert result["written_to_lcr"] is False
    assert "Living Care Record was not changed" in result["message"]

    db = TestSessionLocal()
    try:
        record = db.query(CaptureAnalysis).filter_by(id=body["qwen_analysis_id"]).one()
        assert record.source_text
        assert record.source_event_id == body["id"]
        assert record.candidates["observations"][0]["observation"] == "Mum slept poorly last night."
        assert len(record.review_history) == 1
        assert record.review_history[0]["original_candidate"]["observation"] == "Mum slept badly last night."
        assert record.review_history[0]["reviewed_by_caregiver_id"] == session["caregiver"]["id"]
    finally:
        db.close()


def test_capture_review_rejects_foreign_analysis_and_invalid_candidate_index(monkeypatch):
    client = TestClient(app)
    first = create_session(client, "First care recipient")
    second = create_session(client, "Second care recipient")
    monkeypatch.setattr(api_v1_module, "extract_review_candidates", lambda *args, **kwargs: {
        "observations": [{
            "subject": "sleep",
            "observation": "Caregiver report about sleep.",
            "source_quote": "Caregiver report about sleep.",
            "review_status": "pending_review",
        }],
        "open_questions": [],
        "possible_safety_flags": [],
    })
    saved = client.post(
        "/api/v1/daily/log",
        headers={"Authorization": f"Bearer {first['access_token']}"},
        json={
            "person_id": first["person"]["id"],
            "caregiver_id": first["caregiver"]["id"],
            "notes": "Caregiver report about sleep.",
            "medications": [],
            "vitals": [],
            "scales": [],
        },
    )
    assert saved.status_code == 200, saved.text
    analysis_id = saved.json()["qwen_analysis_id"]
    second_auth = {"Authorization": f"Bearer {second['access_token']}"}

    foreign = client.post(
        f"/api/v1/capture/analyses/{analysis_id}/review",
        headers=second_auth,
        json={"candidate_index": 0, "decision": "accept"},
    )
    assert foreign.status_code == 404, foreign.text

    invalid_index = client.post(
        f"/api/v1/capture/analyses/{analysis_id}/review",
        headers={"Authorization": f"Bearer {first['access_token']}"},
        json={"candidate_index": 4, "decision": "accept"},
    )
    assert invalid_index.status_code == 404, invalid_index.text


def test_capture_review_rejects_repeat_decisions_and_empty_corrections(monkeypatch):
    client = TestClient(app)
    session = create_session(client, "Care recipient")
    monkeypatch.setattr(api_v1_module, "extract_review_candidates", lambda *args, **kwargs: {
        "observations": [{
            "subject": "sleep",
            "observation": "Caregiver report about sleep.",
            "source_quote": "Caregiver report about sleep.",
            "review_status": "pending_review",
        }],
        "open_questions": [],
        "possible_safety_flags": [],
    })
    saved = client.post(
        "/api/v1/daily/log",
        headers={"Authorization": f"Bearer {session['access_token']}"},
        json={
            "person_id": session["person"]["id"],
            "caregiver_id": session["caregiver"]["id"],
            "notes": "Caregiver report about sleep.",
            "medications": [],
            "vitals": [],
            "scales": [],
        },
    )
    assert saved.status_code == 200, saved.text
    analysis_id = saved.json()["qwen_analysis_id"]
    auth = {"Authorization": f"Bearer {session['access_token']}"}

    empty_correction = client.post(
        f"/api/v1/capture/analyses/{analysis_id}/review",
        headers=auth,
        json={"candidate_index": 0, "decision": "correct"},
    )
    assert empty_correction.status_code == 422, empty_correction.text

    accepted = client.post(
        f"/api/v1/capture/analyses/{analysis_id}/review",
        headers=auth,
        json={"candidate_index": 0, "decision": "accept"},
    )
    assert accepted.status_code == 200, accepted.text
    repeat = client.post(
        f"/api/v1/capture/analyses/{analysis_id}/review",
        headers=auth,
        json={"candidate_index": 0, "decision": "reject"},
    )
    assert repeat.status_code == 409, repeat.text
