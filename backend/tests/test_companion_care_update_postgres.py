"""PostgreSQL integration for the conversation-only Companion contract."""
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
from app.models.ai_output_governance import AIOutputLCRProposal
from app.models.care_continuity import CareAssertion
from app.models.collaboration import ConversationMessage
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


def test_postgres_companion_persists_conversation_without_creating_care_updates(monkeypatch):
    Base.metadata.create_all(bind=engine)
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
        Base.metadata.drop_all(bind=engine)
