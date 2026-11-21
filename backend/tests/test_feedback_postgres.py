"""PostgreSQL lifecycle and transaction tests for caregiver improvement feedback.

Feedback is optional and consent-gated. It must be attributed to the authenticated
caregiver, survive a fresh login, redact staged improvement samples, and roll back
all related writes if any part of the operation fails.
"""
import os
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="requires migrated PostgreSQL DATABASE_URL")

from app.main import app
from app.models.collaboration import ConsentRecord
from app.models.improvement_training_example import ImprovementTrainingExample
from app.models.memory import LearningEvent, ReasoningMemory

engine = create_engine(DATABASE_URL, pool_pre_ping=True) if DATABASE_URL else None
Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False) if engine else None


def _create_session(client: TestClient, label: str) -> dict:
    response = client.post(
        "/api/v1/sessions/anonymous",
        json={"name": label, "caregiver_name": "Anonymous caregiver"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _accept_feedback_consent(client: TestClient, created: dict) -> dict[str, str]:
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
            "data_improvement_consent": True,
            "accepted_terms_version": "2026-10",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["verified"] is True
    assert response.json()["profile"]["data_improvement_consent"] is True
    return headers


def test_feedback_persists_across_new_login_is_scoped_redacted_and_atomic(monkeypatch):
    suffix = uuid.uuid4().hex[:12]
    client = TestClient(app)
    created = _create_session(client, f"Feedback recipient {suffix}")
    person_id = created["person"]["id"]
    caregiver_id = created["caregiver"]["id"]
    initial_headers = _accept_feedback_consent(client, created)

    email = f"feedback-{suffix}@example.test"
    password = f"feedback-password-{suffix}"
    account = client.post(
        "/api/v1/accounts",
        headers=initial_headers,
        json={"email": email, "password": password},
    )
    assert account.status_code == 201, account.text

    login = client.post("/api/v1/accounts/login", json={"email": email, "password": password})
    assert login.status_code == 200, login.text
    fresh_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    assert login.json()["person_id"] == person_id
    assert login.json()["caregiver_id"] == caregiver_id

    detail = (
        f"The appointment summary was confusing. Contact feedback-{suffix}@example.test "
        f"on 2026-11-21 or call +1 415 555 0134."
    )
    saved = client.post(
        "/api/v1/feedback",
        headers=fresh_headers,
        json={
            "person_id": person_id,
            "caregiver_id": caregiver_id,
            "category": "help_improve",
            "detail": detail,
        },
    )
    assert saved.status_code == 200, saved.text
    receipt = saved.json()
    assert receipt["event_type"] == "feedback"
    assert receipt["source_type"] == "feedback"
    assert receipt["caregiver_id"] == caregiver_id

    reread = client.get(f"/api/v1/learning-events/{person_id}", headers=fresh_headers)
    assert reread.status_code == 200, reread.text
    assert any(item["id"] == receipt["id"] for item in reread.json())

    with Session() as db:
        event = db.query(LearningEvent).filter_by(id=receipt["id"], person_id=person_id).one()
        memory = db.query(ReasoningMemory).filter_by(
            person_id=person_id,
            memory_type="feedback",
            key="help_improve",
        ).one()
        staged = db.query(ImprovementTrainingExample).filter_by(
            person_id=person_id,
            caregiver_id=caregiver_id,
            category="help_improve",
        ).one()
        consent = db.query(ConsentRecord).filter_by(
            person_id=person_id,
            caregiver_id=caregiver_id,
        ).one()
        assert event.caregiver_id == caregiver_id
        assert memory.value[0]["caregiver_id"] == caregiver_id
        assert staged.status == "pending_review"
        assert staged.consent_version == consent.accepted_terms_version
        assert staged.consented_at is not None
        assert email not in staged.sample_text
        assert "2026-11-21" not in staged.sample_text
        assert "+1 415 555 0134" not in staged.sample_text

    # A second care record cannot submit feedback against the first person's IDs.
    other = _create_session(client, f"Separate feedback recipient {suffix}")
    cross_person = client.post(
        "/api/v1/feedback",
        headers={"Authorization": f"Bearer {other['access_token']}"},
        json={
            "person_id": person_id,
            "caregiver_id": caregiver_id,
            "category": "help_improve",
            "detail": "Cross-person feedback must not be recorded.",
        },
    )
    assert cross_person.status_code == 403, cross_person.text

    # A failure after the memory has been flushed must roll back the memory too.
    import app.api.v1 as v1_api

    def fail_staging(*args, **kwargs):
        raise RuntimeError("synthetic staging failure")

    monkeypatch.setattr(v1_api, "stage_consented_feedback", fail_staging)
    failed = client.post(
        "/api/v1/feedback",
        headers=fresh_headers,
        json={
            "person_id": person_id,
            "caregiver_id": caregiver_id,
            "category": "forced_failure",
            "detail": "This must be rolled back.",
        },
    )
    assert failed.status_code == 503, failed.text

    with Session() as db:
        assert db.query(LearningEvent).filter_by(
            person_id=person_id,
            caregiver_id=caregiver_id,
            event_type="feedback",
            detail="forced_failure: This must be rolled back.",
        ).count() == 0
        assert db.query(ReasoningMemory).filter_by(
            person_id=person_id,
            memory_type="feedback",
            key="forced_failure",
        ).count() == 0
        assert db.query(ImprovementTrainingExample).filter_by(
            person_id=person_id,
            caregiver_id=caregiver_id,
            category="forced_failure",
        ).count() == 0
