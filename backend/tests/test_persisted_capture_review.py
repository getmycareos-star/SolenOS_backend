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
from app.models.care import Evidence
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


def test_daily_capture_correction_then_acceptance_creates_source_linked_lcr_assertion(monkeypatch):
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
    assert result["candidate"]["observation"] == "Mum slept badly last night."
    assert result["candidate"]["corrected_observation"] == "Mum slept poorly last night."
    assert result["candidate"]["review_status"] == "corrected_pending_approval"
    assert result["candidate"]["reviewed_by_caregiver_id"] == session["caregiver"]["id"]
    assert result["written_to_lcr"] is False
    assert "remains pending explicit approval" in result["message"]

    db = TestSessionLocal()
    try:
        assert db.query(CareAssertion).filter_by(person_id=session["person"]["id"]).count() == 0
    finally:
        db.close()

    accepted = client.post(
        f"/api/v1/capture/analyses/{body['qwen_analysis_id']}/review",
        headers=auth,
        json={"candidate_index": 0, "decision": "accept"},
    )
    assert accepted.status_code == 200, accepted.text
    accepted_body = accepted.json()
    assert accepted_body["written_to_lcr"] is True
    assert accepted_body["assertion_id"]
    assert accepted_body["candidate"]["review_status"] == "accepted_by_caregiver"

    repeated = client.post(
        f"/api/v1/capture/analyses/{body['qwen_analysis_id']}/review",
        headers=auth,
        json={"candidate_index": 0, "decision": "accept"},
    )
    assert repeated.status_code == 409, repeated.text

    current_care = client.get("/api/v1/care-continuity/current-care", headers=auth)
    assert current_care.status_code == 200, current_care.text
    assert any(
        item.get("id") == accepted_body["assertion_id"]
        and item.get("description") == "sleep: Mum slept poorly last night."
        for item in current_care.json().get("items", [])
    )

    db = TestSessionLocal()
    try:
        record = db.query(CaptureAnalysis).filter_by(id=body["qwen_analysis_id"]).one()
        assert record.source_text
        assert record.source_event_id == body["id"]
        assert record.execution_status["source_preservation"] == "verified"
        assert record.execution_status["evidence"] == "verified"
        assert record.execution_status["extraction"] == "available"
        assert record.execution_status["review"] == "completed"
        assert record.execution_status["reconciliation"] == "completed"
        assert record.execution_status["longitudinal_retrieval"] in {"available", "unavailable"}
        assert record.candidates["observations"][0]["observation"] == "Mum slept badly last night."
        assert record.candidates["observations"][0]["corrected_observation"] == "Mum slept poorly last night."
        assert len(record.review_history) == 2
        assert record.review_history[0]["original_candidate"]["observation"] == "Mum slept badly last night."
        assert record.review_history[0]["reviewed_by_caregiver_id"] == session["caregiver"]["id"]
        assertion = db.query(CareAssertion).filter_by(id=accepted_body["assertion_id"]).one()
        assert assertion.source_event_id == body["id"]
        assert assertion.evidence_ids == body["evidence_ids"]
        assert assertion.created_by_caregiver_id == session["caregiver"]["id"]
        assert assertion.assertion_status == "caregiver_reviewed"
        assert assertion.asserted_value == "sleep: Mum slept poorly last night."
        assert source in assertion.source_text
    finally:
        db.close()


def test_daily_capture_rejection_never_creates_lcr_assertion_or_allows_retry(monkeypatch):
    client = TestClient(app)
    session = create_session(client, "Rejected care recipient")
    auth = {"Authorization": f"Bearer {session['access_token']}"}
    monkeypatch.setattr(api_v1_module, "extract_review_candidates", lambda *args, **kwargs: {
        "observations": [{
            "subject": "sleep",
            "observation": "Unverified generated suggestion.",
            "source_quote": "Original caregiver wording.",
            "review_status": "pending_review",
        }],
        "open_questions": [],
        "possible_safety_flags": [],
    })
    saved = client.post(
        "/api/v1/daily/log",
        headers=auth,
        json={
            "person_id": session["person"]["id"],
            "caregiver_id": session["caregiver"]["id"],
            "notes": "Original caregiver wording.",
            "medications": [],
            "vitals": [],
            "scales": [],
        },
    )
    assert saved.status_code == 200, saved.text
    analysis_id = saved.json()["qwen_analysis_id"]

    rejected = client.post(
        f"/api/v1/capture/analyses/{analysis_id}/review",
        headers=auth,
        json={"candidate_index": 0, "decision": "reject"},
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["written_to_lcr"] is False
    assert rejected.json()["candidate"]["review_status"] == "rejected_by_caregiver"
    assert rejected.json()["assertion_id"] is None

    for decision in ("accept", "reject"):
        retry = client.post(
            f"/api/v1/capture/analyses/{analysis_id}/review",
            headers=auth,
            json={"candidate_index": 0, "decision": decision},
        )
        assert retry.status_code == 409, retry.text

    db = TestSessionLocal()
    try:
        assert db.query(CareAssertion).filter_by(person_id=session["person"]["id"]).count() == 0
        record = db.query(CaptureAnalysis).filter_by(id=analysis_id).one()
        assert record.candidates["observations"][0]["review_status"] == "rejected_by_caregiver"
        assert len(record.review_history) == 1
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

def test_daily_capture_cannot_promote_model_observation_not_supported_by_source_quote(monkeypatch):
    client = TestClient(app)
    session = create_session(client, "Source validation recipient")
    auth = {"Authorization": f"Bearer {session['access_token']}"}
    source = "Caregiver report: Mum slept badly last night."
    monkeypatch.setattr(api_v1_module, "extract_review_candidates", lambda *args, **kwargs: {
        "observations": [{
            "subject": "sleep",
            "observation": "Mum has severe dementia caused by poor sleep.",
            "source_quote": "Mum slept badly last night.",
            "review_status": "pending_review",
        }],
        "open_questions": [],
        "possible_safety_flags": [],
    })

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
    analysis_id = saved.json()["qwen_analysis_id"]

    accepted = client.post(
        f"/api/v1/capture/analyses/{analysis_id}/review",
        headers=auth,
        json={"candidate_index": 0, "decision": "accept"},
    )
    assert accepted.status_code == 409, accepted.text
    assert "not supported by its preserved source passage" in accepted.json()["detail"]
    assert accepted.json()["detail"].find("no LCR assertion") >= 0

    db = TestSessionLocal()
    try:
        assert db.query(CareAssertion).filter_by(person_id=session["person"]["id"]).count() == 0
        record = db.query(CaptureAnalysis).filter_by(id=analysis_id).one()
        assert record.candidates["observations"][0]["review_status"] == "pending_review"
    finally:
        db.close()


def test_daily_capture_persists_stage_status_and_extracts_saved_evidence(monkeypatch):
    client = TestClient(app)
    session = create_session(client, "Execution status recipient")
    auth = {"Authorization": f"Bearer {session['access_token']}"}
    source_quote = "Mum slept badly last night."
    observed_sources = []

    def extract(source_text, *, source_event_id, source_evidence_id=None):
        observed_sources.append((source_text, source_event_id, source_evidence_id))
        return {
            "observations": [{
                "subject": "sleep",
                "observation": source_quote,
                "source_quote": source_quote,
                "review_status": "pending_review",
            }],
            "open_questions": [],
        }

    monkeypatch.setattr(api_v1_module, "extract_review_candidates", extract)
    saved = client.post(
        "/api/v1/daily/log",
        headers=auth,
        json={
            "person_id": session["person"]["id"],
            "caregiver_id": session["caregiver"]["id"],
            "notes": f"Caregiver report: {source_quote}",
            "medications": [],
            "vitals": [],
            "scales": [],
        },
    )
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["qwen_analysis_id"]
    assert body["execution_status"]["source_preservation"] == "verified"
    assert body["execution_status"]["evidence"] == "verified"
    assert body["execution_status"]["extraction"] == "available"
    assert body["execution_status"]["assessment"] == "pending"
    assert body["execution_status"]["review"] == "required"
    assert body["execution_status"]["reconciliation"] == "completed"
    assert body["execution_status"]["longitudinal_retrieval"] in {"available", "unavailable"}

    db = TestSessionLocal()
    try:
        evidence = db.query(Evidence).filter_by(id=body["evidence_ids"][0]).one()
        analysis = db.query(CaptureAnalysis).filter_by(id=body["qwen_analysis_id"]).one()
        assert observed_sources == [(evidence.source_text, body["id"], evidence.id)]
        assert analysis.source_text == evidence.source_text
        assert analysis.execution_status == body["execution_status"]
    finally:
        db.close()
