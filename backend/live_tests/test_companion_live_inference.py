"""Live Qwen evaluation through the actual Companion HTTP and database path.

This suite is deliberately separate from mocked unit tests. It uses only synthetic
records in an isolated SQLite database and a real Ollama model supplied by CI.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.models.care import CareEvent
from app.models.collaboration import ConversationMessage
from app.main import app
import app.main as main_module


engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


def _create_synthetic_record(client: TestClient):
    response = client.post(
        "/api/v1/sessions/anonymous",
        json={"caregiver_name": "Synthetic live-inference evaluator"},
    )
    assert response.status_code == 201, response.text
    session = response.json()
    person_id = session["person"]["id"]
    caregiver_id = session["caregiver"]["id"]

    db = TestingSessionLocal()
    now = datetime.now(timezone.utc)
    older = CareEvent(
        person_id=person_id,
        event_type="observation",
        status="recorded",
        occurred_at=now - timedelta(days=120),
        title="Sleep history",
        description="Caregiver reported waking several times overnight and restless sleep.",
        evidence_ids=[],
        created_by_caregiver_id=caregiver_id,
        time_provenance="caregiver_reported",
    )
    db.add(older)
    for index in range(45):
        db.add(CareEvent(
            person_id=person_id,
            event_type="observation",
            status="recorded",
            occurred_at=now - timedelta(days=index + 1),
            title=f"Unrelated household note {index}",
            description=f"Household activity note {index}.",
            evidence_ids=[],
            created_by_caregiver_id=caregiver_id,
            time_provenance="caregiver_reported",
        ))
    db.commit()
    older_id = older.id
    db.close()
    return session, older_id


def test_live_qwen_companion_grounding_follow_up_and_missing_information(monkeypatch):
    """Real model inference must pass a small, fixed, evidence-bounded evaluation."""
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = override_db
    monkeypatch.setattr(main_module, "SessionLocal", TestingSessionLocal)
    client = TestClient(app)
    try:
        session, older_event_id = _create_synthetic_record(client)
        token = session["access_token"]
        person_id = session["person"]["id"]
        headers = {"Authorization": f"Bearer {token}"}

        direct = client.post(
            "/api/v1/conversation/message",
            headers=headers,
            json={"person_id": person_id, "text": "What did we record about sleep?"},
        )
        assert direct.status_code == 200, direct.text
        direct_data = direct.json()
        assert direct_data["ai_generated"] is True
        assert older_event_id in direct_data["source_event_ids"]
        assert any(word in direct_data["reply"].casefold() for word in ("sleep", "slept", "waking", "awake"))
        assert direct_data["written_to_lcr"] is False
        assert direct_data["persistence"] == "conversation_only"

        follow_up = client.post(
            "/api/v1/conversation/message",
            headers=headers,
            json={
                "person_id": person_id,
                "session_id": direct_data["session_id"],
                "text": "What about that?",
            },
        )
        assert follow_up.status_code == 200, follow_up.text
        follow_up_data = follow_up.json()
        assert older_event_id in follow_up_data["source_event_ids"]
        assert follow_up_data["written_to_lcr"] is False
        assert follow_up_data["persistence"] == "conversation_only"

        missing = client.post(
            "/api/v1/conversation/message",
            headers=headers,
            json={
                "person_id": person_id,
                "session_id": direct_data["session_id"],
                "text": "What exact blood pressure was recorded last Tuesday?",
            },
        )
        assert missing.status_code == 200, (
            "Real Qwen failed the missing-information response gate instead of "
            f"communicating uncertainty safely: HTTP {missing.status_code} {missing.text}"
        )
        missing_data = missing.json()
        answer = missing_data["reply"].casefold()
        assert any(marker in answer for marker in (
            "not recorded", "not in the record", "does not record", "not documented",
            "not available", "cannot establish", "can't establish", "no blood pressure",
            "doesn't say", "does not say",
        )), f"Qwen did not acknowledge missing evidence: {missing_data['reply']}"
        assert missing_data["written_to_lcr"] is False

        db = TestingSessionLocal()
        try:
            saved = db.query(ConversationMessage).filter_by(session_id=direct_data["session_id"]).all()
            assert [row.role for row in saved] == ["user", "assistant"] * 3
        finally:
            db.close()
    finally:
        client.close()
        app.dependency_overrides.clear()
        Base.metadata.drop_all(bind=engine)
