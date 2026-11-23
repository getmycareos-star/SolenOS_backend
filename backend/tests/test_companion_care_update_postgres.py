"""PostgreSQL integration for Companion conversation and explicit care-report review."""
import importlib
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
from app.models.ai_output_governance import AIOutputLCRDecision, AIOutputLCRProposal
from app.models.care import CareEvent
from app.models.care_continuity import CareAssertion
from app.models.collaboration import ConversationMessage, ConversationSession
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


def test_postgres_companion_persists_conversation_without_creating_care_updates(monkeypatch):
    from sqlalchemy import inspect

    required_tables = {
        ConversationMessage.__tablename__,
        ConversationSession.__tablename__,
        AIOutputLCRProposal.__tablename__,
        CareAssertion.__tablename__,
    }
    missing_tables = required_tables - set(inspect(engine).get_table_names())
    assert not missing_tables, (
        "Companion PostgreSQL integration requires the migration-owned schema; "
        f"missing tables: {sorted(missing_tables)}"
    )
    app.dependency_overrides[get_db] = override_db
    monkeypatch.setattr(main_module, "SessionLocal", TestingSessionLocal)
    captured = []

    def fake_chat(base, model, messages, options=None):
        captured.append(messages)
        return "I can help explain what is already saved. This conversation does not change the care record."

    monkeypatch.setattr(collaboration, "_ollama_url", lambda: ("http://ollama.test", "qwen-test"))
    monkeypatch.setattr(collaboration, "_ollama_chat", fake_chat)
    client = TestClient(app)
    try:
        first = create_session(client, "PostgreSQL caregiver")
        source = "Her sleep has been worse since last Friday."
        turn_id = "companion-conversation-only-0001"
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

        with TestingSessionLocal() as db:
            saved = db.query(ConversationMessage).filter_by(
                session_id=body["session_id"],
                role="user",
                content=source,
            ).all()
            assert len(saved) == 1
            assert db.query(AIOutputLCRProposal).filter_by(person_id=first["person"]["id"]).count() == 0
            assert db.query(CareAssertion).filter_by(person_id=first["person"]["id"]).count() == 0

        second = create_session(client, "Second caregiver")
        other_response = send(client, second, "What is already saved about sleep?", "companion-conversation-only-0002")
        assert other_response.status_code == 200, other_response.text
        with TestingSessionLocal() as db:
            assert db.query(AIOutputLCRProposal).filter_by(person_id=second["person"]["id"]).count() == 0
            assert db.query(CareAssertion).filter_by(person_id=second["person"]["id"]).count() == 0
        assert captured
    finally:
        app.dependency_overrides.clear()
        # The workflow migrates a disposable PostgreSQL database before this test.
        # Keep its migration-owned schema intact; CI discards the database afterward.



def test_postgres_companion_care_report_requires_explicit_review_and_retrieves_in_new_conversation(monkeypatch):
    from sqlalchemy import inspect

    required_tables = {
        ConversationMessage.__tablename__,
        ConversationSession.__tablename__,
        AIOutputLCRProposal.__tablename__,
        AIOutputLCRDecision.__tablename__,
        CareAssertion.__tablename__,
    }
    missing_tables = required_tables - set(inspect(engine).get_table_names())
    assert not missing_tables, (
        "Companion PostgreSQL acceptance requires the migration-owned schema; "
        f"missing tables: {sorted(missing_tables)}"
    )

    app.dependency_overrides[get_db] = override_db
    monkeypatch.setattr(main_module, "SessionLocal", TestingSessionLocal)
    captured_prompts = []

    def fake_chat(base, model, messages, options=None):
        captured_prompts.append(messages)
        return "I can explain what the saved care report says."

    original_builder = collaboration.build_unified_lcr_context

    def include_unsearchable_source(*args, **kwargs):
        context = original_builder(*args, **kwargs)
        context["sources"]["legacy_optional"] = {
            "status": "unsearchable",
            "items": [],
            "candidate_count": None,
            "returned_count": 0,
            "candidate_scan_complete": False,
            "has_more": False,
            "interpretation_limit": (
                "This source cannot be searched for this topic; its lack of returned records "
                "does not establish that no matching care information exists."
            ),
        }
        context["retrieval_scan_complete"] = False
        context["retrieval_projection_complete"] = False
        context["history_coverage"] = "partial_source_history"
        return context

    monkeypatch.setattr(collaboration, "build_unified_lcr_context", include_unsearchable_source)

    # Extraction output is deterministic test input. The real API route,
    # proposal persistence, caregiver decision, reconciliation, provenance,
    # and subsequent LCR retrieval all run against PostgreSQL.
    monkeypatch.setattr(collaboration, "_ollama_url", lambda: ("http://ollama.test", "qwen-test"))
    monkeypatch.setattr(collaboration, "_ollama_chat", fake_chat)
    monkeypatch.setattr(care_updates, "extract_companion_care_candidates", lambda source: {"observations": []})
    client = TestClient(app)

    def review(session, proposal_id, payload):
        return client.post(
            f"/api/v1/companion/care-updates/{proposal_id}/review",
            headers={"Authorization": f"Bearer {session['access_token']}"},
            json=payload,
        )

    def pending(session):
        response = client.get(
            "/api/v1/companion/care-updates",
            headers={"Authorization": f"Bearer {session['access_token']}"},
        )
        assert response.status_code == 200, response.text
        return response.json()["items"]

    try:
        first = create_session(client, "PostgreSQL caregiver")
        headers = {"Authorization": f"Bearer {first['access_token']}"}
        source = "Her sleep has been worse since last Friday."
        response = send(client, first, source, "companion-care-postgres-0001")
        assert response.status_code == 200, response.text
        body = response.json()
        message_id = body["source_message_id"]
        assert message_id
        assert body["written_to_lcr"] is False
        assert body["persistence"] == "conversation_only"
        coverage = body["lcr_context_coverage"]
        assert coverage["history_coverage"] == "partial_source_history"
        assert coverage["sources"]["legacy_optional"]["status"] == "unsearchable"
        assert any(
            item["source_type"] == "legacy_optional"
            and item["status"] == "unsearchable"
            and "cannot be searched for this topic" in item["interpretation_limit"]
            for item in coverage["source_limits"]
        )
        assert any(
            item["source_type"] == "legacy_optional"
            and item["status"] == "unsearchable"
            for item in coverage["retrieval_receipt"]["source_limits"]
        )
        assert captured_prompts
        assert "cannot be searched for this topic" in captured_prompts[0][-1]["content"]

        missing_confirmation = client.post(
            f"/api/v1/companion/care-updates/from-message/{message_id}",
            headers=headers,
            json={"confirm_care_report": False},
        )
        assert missing_confirmation.status_code == 422

        created = client.post(
            f"/api/v1/companion/care-updates/from-message/{message_id}",
            headers=headers,
            json={"confirm_care_report": True},
        )
        assert created.status_code == 200, created.text
        proposal = created.json()["proposal"]
        assert proposal["status"] == "pending_authorization"
        assert proposal["proposed_change"]["asserted_value"] == source
        assert proposal["proposed_change"]["source_quote"] == source
        assert proposal["proposed_change"]["information_status"] == "caregiver_reported_pending_review"
        assert created.json()["idempotent_replay"] is False

        replay_create = client.post(
            f"/api/v1/companion/care-updates/from-message/{message_id}",
            headers=headers,
            json={"confirm_care_report": True},
        )
        assert replay_create.status_code == 200, replay_create.text
        assert replay_create.json()["idempotent_replay"] is True
        assert replay_create.json()["proposal"]["id"] == proposal["id"]

        queued = pending(first)
        assert len(queued) == 1
        assert queued[0]["id"] == proposal["id"]

        with TestingSessionLocal() as db:
            assert db.query(CareAssertion).filter_by(person_id=first["person"]["id"]).count() == 0

        second = create_session(client, "Second caregiver")
        cross_person_create = client.post(
            f"/api/v1/companion/care-updates/from-message/{message_id}",
            headers={"Authorization": f"Bearer {second['access_token']}"},
            json={"confirm_care_report": True},
        )
        assert cross_person_create.status_code == 404
        assert pending(second) == []
        cross_person_review = review(second, proposal["id"], {"decision": "accept"})
        assert cross_person_review.status_code == 404

        accepted = review(first, proposal["id"], {"decision": "accept"})
        assert accepted.status_code == 200, accepted.text
        accepted_body = accepted.json()
        assert accepted_body["written_to_lcr"] is True
        assert accepted_body["decision"] == "accept"
        assertion_id = accepted_body["assertion_id"]
        assert assertion_id

        replay_review = review(first, proposal["id"], {"decision": "accept"})
        assert replay_review.status_code == 200, replay_review.text
        assert replay_review.json()["idempotent_replay"] is True
        assert replay_review.json()["status"] == "accepted"
        assert replay_review.json()["decision"] == "accept"
        assert replay_review.json()["written_to_lcr"] is True
        assert replay_review.json()["assertion_id"] == assertion_id

        current_care = client.get(
            "/api/v1/care-continuity/current-care?limit_per_source=50",
            headers=headers,
        )
        assert current_care.status_code == 200, current_care.text
        assert any(item.get("id") == assertion_id for item in current_care.json().get("items", []))

        second_source = "She did not finish lunch yesterday."
        second_message = send(client, first, second_source, "companion-care-postgres-0002")
        assert second_message.status_code == 200, second_message.text
        second_message_id = second_message.json()["source_message_id"]
        second_created = client.post(
            f"/api/v1/companion/care-updates/from-message/{second_message_id}",
            headers=headers,
            json={"confirm_care_report": True},
        )
        assert second_created.status_code == 200, second_created.text
        second_proposal_id = second_created.json()["proposal"]["id"]
        rejected = review(first, second_proposal_id, {"decision": "reject"})
        assert rejected.status_code == 200, rejected.text
        assert rejected.json()["proposal"]["status"] == "rejected"
        assert rejected.json()["decision"] == "reject"
        assert rejected.json()["written_to_lcr"] is False
        rejected_replay = review(first, second_proposal_id, {"decision": "reject"})
        assert rejected_replay.status_code == 200, rejected_replay.text
        assert rejected_replay.json()["idempotent_replay"] is True
        assert rejected_replay.json()["status"] == "rejected"
        assert rejected_replay.json()["decision"] == "reject"
        assert rejected_replay.json()["written_to_lcr"] is False
        with TestingSessionLocal() as db:
            assert db.query(CareAssertion).filter_by(person_id=first["person"]["id"]).count() == 1

        prompt_count_before_readback = len(captured_prompts)
        follow_up = client.post(
            "/api/v1/conversation/message",
            headers=headers,
            json={
                "person_id": first["person"]["id"],
                "text": "What have I told you about her sleep?",
                # No session_id: this must create a new conversation and use
                # persisted LCR evidence rather than replaying the original chat.
                "client_turn_id": "companion-readback-0001",
            },
        )
        assert follow_up.status_code == 200, follow_up.text
        assert follow_up.json()["session_id"] != body["session_id"]
        assert source in follow_up.json()["reply"]
        assert follow_up.json()["ai_generated"] is False
        assert follow_up.json()["response_mode"] == "source_quoted_care_history"
        assert len(captured_prompts) == prompt_count_before_readback

        prompt_count_before_natural_recall = len(captured_prompts)
        natural_recall = client.post(
            "/api/v1/conversation/message",
            headers=headers,
            json={
                "person_id": first["person"]["id"],
                "text": "What did I mention about her sleep?",
                "client_turn_id": "companion-readback-0002",
            },
        )
        assert natural_recall.status_code == 200, natural_recall.text
        assert source in natural_recall.json()["reply"]
        assert natural_recall.json()["ai_generated"] is False
        assert natural_recall.json()["response_mode"] == "source_quoted_care_history"
        assert len(captured_prompts) == prompt_count_before_natural_recall

        with TestingSessionLocal() as db:
            assertion = db.query(CareAssertion).filter_by(id=assertion_id).one()
            assert assertion.person_id == first["person"]["id"]
            assert assertion.asserted_value == source
            assert assertion.assertion_status == "caregiver_reviewed"
            assert assertion.epistemic_state == "reported"
            assert assertion.source_message_id == message_id
            assert assertion.source_proposal_id == proposal["id"]
            assert assertion.source_conversation_id == body["session_id"]

            saved_proposal = db.query(AIOutputLCRProposal).filter_by(id=proposal["id"]).one()
            assert saved_proposal.status == "accepted"
            decisions = db.query(AIOutputLCRDecision).filter_by(proposal_id=proposal["id"]).all()
            assert len(decisions) == 1
            assert decisions[0].decision == "accepted"
    finally:
        app.dependency_overrides.clear()


def test_postgres_companion_direct_event_context_marks_withdrawn_events_as_historical(monkeypatch):
    from datetime import datetime, timezone

    app.dependency_overrides[get_db] = override_db
    monkeypatch.setattr(main_module, "SessionLocal", TestingSessionLocal)
    captured_prompts = []

    def fake_chat(base, model, messages, options=None):
        captured_prompts.extend(messages)
        return "I can help explain the saved care information and its limits."

    monkeypatch.setattr(collaboration, "_ollama_url", lambda: ("http://ollama.test", "qwen-test"))
    monkeypatch.setattr(collaboration, "_ollama_chat", fake_chat)
    client = TestClient(app)
    try:
        session = create_session(client, "Historical event caregiver")
        person_id = session["person"]["id"]
        caregiver_id = session["caregiver"]["id"]
        event = CareEvent(
            person_id=person_id,
            created_by_caregiver_id=caregiver_id,
            event_type="daily_observation",
            status="withdrawn",
            occurred_at=datetime(2026, 11, 20, tzinfo=timezone.utc),
            title="Corrected sleep report",
            description="The caregiver withdrew this earlier sleep report.",
            evidence_ids=[],
            time_provenance="caregiver_reported",
        )
        with TestingSessionLocal() as db:
            db.add(event)
            db.commit()

        response = send(
            client,
            session,
            "Please help me understand the care information saved for her.",
            "00000000-0000-4000-8000-000000000001",
        )
        assert response.status_code == 200, response.text
        prompt = "\\n".join(
            str(message.get("content") or "") for message in captured_prompts
            if isinstance(message, dict)
        )
        assert "historical_event" in prompt
        assert "do not present it as current care information" in prompt
        assert "recorded_care_event" not in prompt
    finally:
        app.dependency_overrides.clear()
