"""PostgreSQL integration for Companion-to-LCR review; requires an isolated migrated database."""
import importlib
import json
import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

DATABASE_URL = os.getenv("SOLENOS_COMPANION_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="requires isolated migrated PostgreSQL database")

engine = create_engine(DATABASE_URL, pool_pre_ping=True) if DATABASE_URL else None
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine) if engine is not None else None
from app.core.database import Base, get_db
from app.models.ai_output_governance import AIOutputLCRProposal
from app.models.care_continuity import CareAssertion
from app.models.collaboration import ConversationMessage
from app.models.qwen_background_job import QwenBackgroundJob, QwenModelOutput, QwenSafetyEvaluation
main_module = importlib.import_module("app.main")
collaboration = importlib.import_module("app.api.collaboration")
app = main_module.app


def override_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


def create_session(client, name):
    response = client.post("/api/v1/sessions/anonymous", json={"caregiver_name": name})
    assert response.status_code == 201, response.text
    return response.json()


def send(client, session, message, turn_id):
    return client.post(
        "/api/v1/conversation/message",
        headers={"Authorization": f"Bearer {session['access_token']}"},
        json={"person_id": session["person"]["id"], "text": message, "client_turn_id": turn_id},
    )


def review(client, session, proposal_id, payload):
    return client.post(
        f"/api/v1/companion/care-updates/{proposal_id}/review",
        headers={"Authorization": f"Bearer {session['access_token']}"},
        json=payload,
    )


def pending(client, session):
    response = client.get(
        "/api/v1/companion/care-updates",
        headers={"Authorization": f"Bearer {session['access_token']}"},
    )
    assert response.status_code == 200, response.text
    return response.json()["items"]


def test_postgres_companion_messages_remain_conversation_only(monkeypatch):
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = override_db
    monkeypatch.setattr(main_module, "SessionLocal", TestingSessionLocal)
    monkeypatch.setattr(collaboration, "_ollama_url", lambda: ("http://ollama.test", "qwen-test"))
    monkeypatch.setattr(
        collaboration,
        "_ollama_chat",
        lambda base, model, messages, options=None: "I can help you understand the care information already saved.",
    )

    def extraction_must_not_run(*args, **kwargs):
        raise AssertionError("PostgreSQL Companion endpoint must not invoke care-update extraction")

    monkeypatch.setattr(collaboration, "process_companion_care_update_message", extraction_must_not_run, raising=False)
    client = TestClient(app)
    try:
        first = create_session(client, "PostgreSQL caregiver")
        first_auth = {"Authorization": f"Bearer {first['access_token']}"}
        source = "The caregiver recorded a change during the morning routine."
        turn_id = "companion-pg-conversation-only-0001"

        response = send(client, first, source, turn_id)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["care_update_candidates"] == []
        assert body["written_to_lcr"] is False
        assert body["persistence"] == "conversation_only"

        replay = send(client, first, source, turn_id)
        assert replay.status_code == 200, replay.text
        assert replay.json()["idempotent_replay"] is True
        assert replay.json()["care_update_candidates"] == []

        pending_list = client.get("/api/v1/companion/care-updates", headers=first_auth)
        assert pending_list.status_code == 200, pending_list.text
        assert pending_list.json()["items"] == []

        with TestingSessionLocal() as db:
            saved = db.query(ConversationMessage).filter_by(
                session_id=body["session_id"], role="user", content=source
            ).one()
            assert saved.content == source
            assert db.query(AIOutputLCRProposal).filter_by(person_id=first["person"]["id"]).count() == 0
            assert db.query(CareAssertion).filter_by(person_id=first["person"]["id"]).count() == 0

        # Another recipient's session cannot see the first recipient's conversation.
        second = create_session(client, "Different PostgreSQL caregiver")
        second_auth = {"Authorization": f"Bearer {second['access_token']}"}
        second_pending = client.get("/api/v1/companion/care-updates", headers=second_auth)
        assert second_pending.status_code == 200, second_pending.text
        assert second_pending.json()["items"] == []
        with TestingSessionLocal() as db:
            assert db.query(ConversationMessage).filter_by(
                session_id=body["session_id"], role="user", content=source
            ).count() == 1
            assert db.query(AIOutputLCRProposal).filter_by(person_id=second["person"]["id"]).count() == 0
    finally:
        client.close()
        app.dependency_overrides.clear()


def test_postgres_qwen_assistance_summary_requires_explicit_caregiver_approval():
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = override_db
    client = TestClient(app)
    try:
        session = create_session(client, "Qwen summary reviewer")
        headers = {"Authorization": f"Bearer {session['access_token']}"}
        recorded = client.post(
            "/api/v1/daily/log",
            headers=headers,
            json={
                "person_id": session["person"]["id"],
                "caregiver_id": session["caregiver"]["id"],
                "capture_idempotency_key": "qwen-review-source-0001",
                "observation": "Caregiver reported that the morning medication was taken.",
            },
        )
        assert recorded.status_code in (200, 201), recorded.text
        event_id = recorded.json()["id"]
        evidence_ids = recorded.json().get("evidence_ids", [])
        assert evidence_ids

        with TestingSessionLocal() as db:
            job = QwenBackgroundJob(
                person_id=session["person"]["id"],
                caregiver_id=session["caregiver"]["id"],
                status="completed",
                task_text="Summarize the saved medication observation.",
                context_text="",
                workflow="medication_day",
                source_event_ids_json="[" + json.dumps(event_id) + "]",
                source_evidence_ids_json=json.dumps(evidence_ids),
            )
            db.add(job)
            db.flush()
            output = QwenModelOutput(
                job_id=job.id,
                person_id=session["person"]["id"],
                caregiver_id=session["caregiver"]["id"],
                workflow="medication_day",
                model_name="qwen-test",
                output_text="The caregiver reported that the morning medication was taken.",
                output_metadata={},
                source_event_ids_json=json.dumps([event_id]),
                source_evidence_ids_json=__import__("json").dumps(evidence_ids),
                review_status="pending_review",
                review_required=True,
                written_to_lcr=False,
            )
            db.add(output)
            db.flush()
            db.add(QwenSafetyEvaluation(
                output_id=output.id,
                job_id=job.id,
                person_id=session["person"]["id"],
                caregiver_id=session["caregiver"]["id"],
                risk_class="unassessed",
                decision="review_required",
                human_review_required=True,
                violations_json=[],
                policy_references_json=[],
            ))
            db.commit()
            job_id = job.id

        before = client.get("/api/v1/care-continuity/current-care", headers=headers)
        assert before.status_code == 200, before.text
        assert not any(item.get("asserted_value") == "The caregiver reported that the morning medication was taken." for item in before.json().get("items", []))

        prepared = client.post(f"/api/v1/assistant/jobs/{job_id}/proposal", headers=headers, json={})
        assert prepared.status_code == 201, prepared.text
        proposal = prepared.json()["proposal"]
        assert proposal["status"] == "pending_authorization"
        assert proposal["proposed_change"]["source_event_ids"] == [event_id]
        assert proposal["proposed_change"]["source_evidence_ids"] == evidence_ids

        # Preparing a proposal must not write an assertion.
        with TestingSessionLocal() as db:
            assert db.query(CareAssertion).filter_by(person_id=session["person"]["id"]).count() == 0

        accepted = client.post(
            f"/api/v1/companion/care-updates/qwen/{proposal['id']}/review",
            headers=headers,
            json={"decision": "accept"},
        )
        assert accepted.status_code == 200, accepted.text
        result = accepted.json()
        assert result["status"] == "accepted"
        assert result["written_to_lcr"] is True
        assert result["epistemic_state"] == "inferred"
        assert result["assertion_id"]

        replay = client.post(
            f"/api/v1/companion/care-updates/qwen/{proposal['id']}/review",
            headers=headers,
            json={"decision": "accept"},
        )
        assert replay.status_code == 200, replay.text
        assert replay.json()["idempotent_replay"] is True

        with TestingSessionLocal() as db:
            assertion = db.query(CareAssertion).filter_by(id=result["assertion_id"]).one()
            saved_proposal = db.query(AIOutputLCRProposal).filter_by(id=proposal["id"]).one()
            saved_output = db.query(QwenModelOutput).filter_by(job_id=job_id).one()
            assert assertion.assertion_status == "caregiver_reviewed"
            assert assertion.epistemic_state == "inferred"
            assert assertion.evidence_ids == evidence_ids
            assert assertion.source_proposal_id == proposal["id"]
            assert saved_proposal.status == "accepted"
            assert saved_output.review_status == "accepted"
            assert saved_output.written_to_lcr is True

        foreign = create_session(client, "Unrelated Qwen reviewer")
        denied = client.post(
            f"/api/v1/companion/care-updates/qwen/{proposal['id']}/review",
            headers={"Authorization": f"Bearer {foreign['access_token']}"},
            json={"decision": "accept"},
        )
        assert denied.status_code == 404
    finally:
        client.close()
        app.dependency_overrides.clear()
