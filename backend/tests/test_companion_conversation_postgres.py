"""PostgreSQL continuity and authorization tests for Companion conversations."""
import os
import uuid
import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="requires migrated PostgreSQL DATABASE_URL")

from app.main import app
from app.models.collaboration import ConversationMessage, ConversationSession

engine = create_engine(DATABASE_URL, pool_pre_ping=True) if DATABASE_URL else None
Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False) if engine else None


def _new_anonymous(client: TestClient, label: str) -> dict:
    response = client.post(
        "/api/v1/sessions/anonymous",
        json={"name": label, "caregiver_name": "Primary caregiver"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_companion_history_and_source_references_survive_new_login_and_remain_scoped():
    suffix = uuid.uuid4().hex[:10]
    client = TestClient(app)
    created = _new_anonymous(client, f"Companion recipient {suffix}")
    person_id = created["person"]["id"]
    caregiver_id = created["caregiver"]["id"]
    initial_headers = {"Authorization": f"Bearer {created['access_token']}"}

    email = f"companion-{suffix}@example.test"
    password = f"companion-password-{suffix}"
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

    session_id = f"companion-{suffix}"
    message_id = f"companion-message-{suffix}"
    source_refs = [
        {
            "record_type": "care_event",
            "record_id": f"event-{suffix}",
            "relationship": "supports",
        },
        {
            "record_type": "evidence",
            "record_id": f"evidence-{suffix}",
            "relationship": "source",
        },
    ]
    now = datetime.now(timezone.utc)
    with Session() as db:
        db.add(ConversationSession(
            id=session_id,
            person_id=person_id,
            caregiver_id=caregiver_id,
            title="Sleep and night waking",
            status="active",
            created_at=now,
            updated_at=now,
        ))
        db.flush()
        db.add(ConversationMessage(
            id=message_id,
            session_id=session_id,
            role="assistant",
            content="The saved record contains a caregiver report about night waking.",
            guard_verdict="supported",
            safety_note=None,
            evidence_ids_json=json.dumps([f"evidence-{suffix}"]),
            source_record_refs_json=json.dumps(source_refs),
            response_metadata_json=json.dumps({
                "source_event_ids": [f"event-{suffix}"],
                "source_evidence_ids": [f"evidence-{suffix}"],
                "ai_generated": False,
                "response_mode": "record_retrieval",
            }),
            created_at=now,
        ))
        db.commit()

    listed = client.get("/api/v1/conversations", headers=fresh_headers)
    assert listed.status_code == 200, listed.text
    item = next(row for row in listed.json()["items"] if row["session_id"] == session_id)
    assert item["person_id"] == person_id
    assert item["message_count"] == 1
    assert item["title"] == "Sleep and night waking"

    history = client.get(f"/api/v1/conversation/{session_id}", headers=fresh_headers)
    assert history.status_code == 200, history.text
    assert history.json()["person_id"] == person_id
    message = next(row for row in history.json()["messages"] if row["id"] == message_id)
    assert message["text"] == "The saved record contains a caregiver report about night waking."
    assert message["evidence_ids"] == [f"evidence-{suffix}"]
    assert message["source_record_refs"] == source_refs
    assert message["source_event_ids"] == [f"event-{suffix}"]
    assert message["source_evidence_ids"] == [f"evidence-{suffix}"]
    assert message["ai_generated"] is False
    assert message["response_mode"] == "record_retrieval"

    other = _new_anonymous(client, f"Separate Companion recipient {suffix}")
    other_headers = {"Authorization": f"Bearer {other['access_token']}"}
    other_list = client.get("/api/v1/conversations", headers=other_headers)
    assert other_list.status_code == 200, other_list.text
    assert all(row["session_id"] != session_id for row in other_list.json()["items"])
    cross_person_history = client.get(f"/api/v1/conversation/{session_id}", headers=other_headers)
    assert cross_person_history.status_code == 404, cross_person_history.text

    with Session() as db:
        stored_session = db.query(ConversationSession).filter_by(id=session_id).one()
        stored_message = db.query(ConversationMessage).filter_by(id=message_id).one()
        assert stored_session.person_id == person_id
        assert stored_session.caregiver_id == caregiver_id
        assert json.loads(stored_message.source_record_refs_json) == source_refs
