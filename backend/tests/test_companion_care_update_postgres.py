"""PostgreSQL integration for Companion-to-LCR review; requires an isolated migrated database."""
import importlib
import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

DATABASE_URL = os.getenv("SOLENOS_COMPANION_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="requires isolated migrated PostgreSQL database")

engine = create_engine(DATABASE_URL, pool_pre_ping=True) if DATABASE_URL else None
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine) if engine is not None else None
from app.core.database import Base, get_db
from app.models.ai_output_governance import AIOutputLCRDecision, AIOutputLCRProposal
from app.models.care_continuity import CareAssertion
from app.models.collaboration import ConversationMessage
main_module = importlib.import_module("app.main")
collaboration = importlib.import_module("app.api.collaboration")
care_updates = importlib.import_module("app.services.companion_care_updates")
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


def test_postgres_companion_care_update_lifecycle(monkeypatch):
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = override_db
    monkeypatch.setattr(main_module, "SessionLocal", TestingSessionLocal)
    captured = []

    def fake_chat(base, model, messages, options=None):
        captured.append(messages)
        return "I have noted what you shared. I can help you keep the care information clear."

    monkeypatch.setattr(collaboration, "_ollama_url", lambda: ("http://ollama.railway.internal", "qwen-test"))
    monkeypatch.setattr(collaboration, "_ollama_chat", fake_chat)

    event_time_text = "2026-11-06T18:00:00+01:00"
    original = "The caregiver recorded a change during the morning routine at " + event_time_text + "."
    monkeypatch.setattr(
        care_updates,
        "extract_companion_care_candidates",
        lambda source: {"observations": [{"source_quote": original, "event_time_quote": event_time_text}] if source == original else [],
    )
    client = TestClient(app)
    try:
        first = create_session(client, "PostgreSQL caregiver")
        first_auth = {"Authorization": f"Bearer {first['access_token']}"}
        first_turn = "companion-pg-accept-0001"
        response = send(client, first, original, first_turn)
        assert response.status_code == 200, response.text
        body = response.json()
        proposal = pending(client, first)[0]
        assert proposal["status"] == "pending_authorization"
        assert proposal["source_message_id"]
        pending_list = client.get("/api/v1/companion/care-updates", headers=first_auth)
        assert pending_list.status_code == 200, pending_list.text
        assert [item["id"] for item in pending_list.json()["items"]] == [proposal["id"]]
        before_accept = client.get("/api/v1/care-continuity/current-care", headers=first_auth)
        assert before_accept.status_code == 200, before_accept.text
        assert not any(item.get("id") == proposal["id"] for item in before_accept.json().get("items", []))

        replay = send(client, first, original, first_turn)
        assert replay.status_code == 200, replay.text
        assert replay.json()["idempotent_replay"] is True
        assert len(replay.json()["care_update_candidates"]) == 1

        with TestingSessionLocal() as db:
            source_message = db.query(ConversationMessage).filter_by(id=proposal["source_message_id"]).one()
            pending = db.query(AIOutputLCRProposal).filter_by(id=proposal["id"]).one()
            assert source_message.content == original
            assert pending.source_message_id == source_message.id
            assert pending.source_conversation_id == body["session_id"]
            assert db.query(CareAssertion).filter_by(person_id=first["person"]["id"]).count() == 0

        accepted = review(client, first, proposal["id"], {"decision": "accept"})
        assert accepted.status_code == 200, accepted.text
        assertion_id = accepted.json()["assertion_id"]
        assert assertion_id
        repeated = review(client, first, proposal["id"], {"decision": "accept"})
        assert repeated.status_code == 200 and repeated.json()["idempotent_replay"] is True

        current = client.get("/api/v1/care-continuity/current-care", headers=first_auth)
        assert current.status_code == 200, current.text
        assert any(item.get("id") == assertion_id for item in current.json().get("items", []))
        with TestingSessionLocal() as db:
            assertion = db.query(CareAssertion).filter_by(id=assertion_id).one()
            assert assertion.event_time_text == event_time_text
            assert assertion.observed_at is not None
            assert assertion.recorded_at is not None
            assert assertion.observed_at != assertion.recorded_at

        # Refresh the bearer capability, then verify a later authenticated Companion turn retrieves the accepted assertion.
        refreshed = client.post("/api/v1/sessions/refresh", headers=first_auth)
        assert refreshed.status_code == 200, refreshed.text
        first["access_token"] = refreshed.json()["access_token"]
        first_auth = {"Authorization": f"Bearer {first['access_token']}"}

        # A later Companion turn must receive the accepted assertion through the shared retrieval path.
        monkeypatch.setattr(care_updates, "extract_companion_care_candidates", lambda source: {"observations": []})
        follow_up = send(client, first, "What has been recorded about this observation?", "companion-pg-followup-0002")
        assert follow_up.status_code == 200, follow_up.text
        prompt_text = "\n".join(str(item.get("content", "")) for item in captured[-1])
        assert original in prompt_text

        # Correction is a new proposal version and cannot write until a second explicit acceptance.
        corrected_source = "The caregiver recorded a second observation during the visit."
        monkeypatch.setattr(
            care_updates,
            "extract_companion_care_candidates",
            lambda source: {"observations": [{"source_quote": corrected_source}] if source == corrected_source else [],
        )
        correction_turn = send(client, first, corrected_source, "companion-pg-correction-0003")
        assert correction_turn.status_code == 200, correction_turn.text
        correction_proposal = pending(client, first)[0]
        corrected_text = "The caregiver recorded a clarified second observation during the visit."
        correction_context = "Caregiver clarification: " + corrected_text
        corrected = review(client, first, correction_proposal["id"], {
            "decision": "correct", "corrected_text": corrected_text, "correction_context": correction_context,
        })
        assert corrected.status_code == 200, corrected.text
        new_proposal = corrected.json()["corrected_proposal"]
        assert new_proposal["status"] == "pending_authorization"
        with TestingSessionLocal() as db:
            assert db.query(CareAssertion).filter_by(person_id=first["person"]["id"]).count() == 1
            old = db.query(AIOutputLCRProposal).filter_by(id=correction_proposal["id"]).one()
            assert old.proposed_change_json["asserted_value"] == corrected_source
            assert old.status == "corrected"
        accepted_correction = review(client, first, new_proposal["id"], {"decision": "accept"})
        assert accepted_correction.status_code == 200, accepted_correction.text
        correction_history = client.get(
            f"/api/v1/companion/care-updates/{correction_proposal['id']}/history",
            headers=first_auth,
        )
        assert correction_history.status_code == 200, correction_history.text
        assert correction_history.json()["append_only"] is True
        correction_decision = correction_history.json()["items"][0]
        assert correction_decision["decision"] == "corrected"
        assert correction_decision["reviewer_caregiver_id"] == first["caregiver"]["id"]
        assert correction_decision["correction_context"] == correction_context
        with TestingSessionLocal() as db:
            corrected_assertion = db.query(CareAssertion).filter_by(source_proposal_id=new_proposal["id"]).one()
            assert corrected_assertion.asserted_value == corrected_text
            assert corrected_source in corrected_assertion.source_text
            assert correction_context in corrected_assertion.source_text

        # Rejection is terminal and must never create an assertion.
        rejected_source = "The caregiver recorded a separate event for review."
        monkeypatch.setattr(
            care_updates,
            "extract_companion_care_candidates",
            lambda source: {"observations": [{"source_quote": rejected_source}] if source == rejected_source else [],
        )
        rejected_turn = send(client, first, rejected_source, "companion-pg-reject-0004")
        assert rejected_turn.status_code == 200, rejected_turn.text
        rejected_proposal = pending(client, first)[0]
        rejected = review(client, first, rejected_proposal["id"], {"decision": "reject"})
        assert rejected.status_code == 200 and rejected.json()["written_to_lcr"] is False
        rejection_history = client.get(
            f"/api/v1/companion/care-updates/{rejected_proposal['id']}/history",
            headers=first_auth,
        )
        assert rejection_history.status_code == 200, rejection_history.text
        assert rejection_history.json()["items"][0]["decision"] == "rejected"
        retry = review(client, first, rejected_proposal["id"], {"decision": "accept"})
        assert retry.status_code == 409
        with TestingSessionLocal() as db:
            assert db.query(CareAssertion).filter_by(source_proposal_id=rejected_proposal["id"]).count() == 0
            assert db.query(AIOutputLCRProposal).filter_by(id=rejected_proposal["id"]).one().status == "rejected"

        # Another care recipient cannot read or decide the first caregiver's proposal.
        second = create_session(client, "Different PostgreSQL caregiver")
        foreign = review(client, second, rejected_proposal["id"], {"decision": "accept"})
        assert foreign.status_code == 404

        # Candidate inference failure does not undo the persisted user message or Companion reply.
        monkeypatch.setattr(
            collaboration,
            "extract_companion_care_candidates",
            lambda source: (_ for _ in ()).throw(RuntimeError("inference unavailable")),
        )
        failed_extraction_source = "A separate caregiver observation whose extraction is temporarily unavailable."
        failed = send(client, first, failed_extraction_source, "companion-pg-failure-0005")
        assert failed.status_code == 200, failed.text
        assert failed.json()["care_update_candidates"] == []
        with TestingSessionLocal() as db:
            assert db.query(ConversationMessage).filter_by(
                session_id=failed.json()["session_id"], role="user", content=failed_extraction_source
            ).count() == 1
            assert db.query(CareAssertion).filter_by(person_id=first["person"]["id"]).count() == 2

            # The database trigger makes review decisions append-only, not merely ORM-convention append-only.
            accepted_decision = db.query(AIOutputLCRDecision).filter_by(proposal_id=proposal["id"]).one()
            with pytest.raises(DBAPIError):
                db.execute(
                    text("UPDATE ai_output_lcr_decisions SET rationale = 'tampered' WHERE id = :decision_id"),
                    {"decision_id": accepted_decision.id},
                )
                db.commit()
            db.rollback()
    finally:
        client.close()
        app.dependency_overrides.clear()
