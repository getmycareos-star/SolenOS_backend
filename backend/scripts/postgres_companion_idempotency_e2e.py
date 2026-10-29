"""PostgreSQL-backed Companion idempotency, concurrency, and isolation checks.

Runs only against the disposable PostgreSQL database supplied by CI. Qwen is
replaced with a deterministic test double here; live-model evaluation is a
separate release gate.
"""
from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient

DATABASE_URL = os.environ.get("DATABASE_URL", "")
if not DATABASE_URL.startswith(("postgresql://", "postgresql+psycopg2://")):
    raise SystemExit("Refusing Companion PostgreSQL E2E: DATABASE_URL must be PostgreSQL.")

from app.core.database import get_db
from app.main import app
from app.api import collaboration
from app.models.collaboration import ConversationMessage

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def override_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def main():
    app.dependency_overrides[get_db] = override_db
    client = TestClient(app)
    barrier = threading.Barrier(2)
    qwen_calls = []
    qwen_lock = threading.Lock()

    def fake_qwen(base, model, messages, options=None):
        with qwen_lock:
            qwen_calls.append(messages)
        # Ensure both concurrent requests pass the idempotency lookup and enter
        # inference before either can commit the unique turn key.
        barrier.wait(timeout=20)
        return "The saved record does not establish an answer to that question."

    original_url = collaboration._ollama_url
    original_chat = collaboration._ollama_chat
    collaboration._ollama_url = lambda: ("http://ollama.test.invalid", "qwen-test")
    collaboration._ollama_chat = fake_qwen
    try:
        status, first_session = client.post(
            "/api/v1/sessions/anonymous",
            json={"name": "CI Companion person A", "caregiver_name": "CI Companion caregiver A"},
        ).status_code, None
        # Keep the response payload explicit so assertion messages include the API body.
        created = client.post(
            "/api/v1/sessions/anonymous",
            json={"name": "CI Companion person B", "caregiver_name": "CI Companion caregiver B"},
        )
        require(status == 201, f"anonymous session A failed: {status}")
        require(created.status_code == 201, f"anonymous session B failed: {created.status_code}: {created.text}")
        # Fetch a distinct first session; the initial status-only request above is
        # deliberately not used as an identity source.
        created_a = client.post(
            "/api/v1/sessions/anonymous",
            json={"name": "CI Companion person C", "caregiver_name": "CI Companion caregiver C"},
        )
        require(created_a.status_code == 201, f"anonymous session C failed: {created_a.status_code}: {created_a.text}")
        data_a, data_b = created_a.json(), created.json()
        token_a, person_a = data_a["access_token"], data_a["person"]["id"]
        token_b, person_b = data_b["access_token"], data_b["person"]["id"]
        turn_id = "a1d6a0b7-9e62-4f44-8b4b-6f2d9a9b4c31"
        payload = {
            "person_id": person_a,
            "text": "What does the saved record establish?",
            "client_turn_id": turn_id,
        }

        def send():
            return client.post(
                "/api/v1/conversation/message",
                headers={"Authorization": f"Bearer {token_a}"},
                json=payload,
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: send(), range(2)))
        statuses = sorted(response.status_code for response in results)
        require(statuses == [200, 503], f"concurrent same-key turns should yield one commit and one retryable conflict: {statuses}")
        winner = next(response.json() for response in results if response.status_code == 200)
        require(winner.get("written_to_lcr") is False, "Companion turn must not write to LCR")
        require(winner.get("session_id"), "committed turn must return its session ID")

        calls_after_race = len(qwen_calls)
        replay = client.post(
            "/api/v1/conversation/message",
            headers={"Authorization": f"Bearer {token_a}"},
            json=payload,
        )
        require(replay.status_code == 200, f"retry after concurrent conflict failed: {replay.status_code}: {replay.text}")
        replay_data = replay.json()
        require(replay_data.get("idempotent_replay") is True, "retry must replay committed response")
        require(replay_data.get("session_id") == winner["session_id"], "retry must preserve original session")
        require(replay_data.get("reply") == winner.get("reply"), "retry must return exact committed answer")
        require(len(qwen_calls) == calls_after_race, "idempotent replay must not invoke Qwen again")

        changed = client.post(
            "/api/v1/conversation/message",
            headers={"Authorization": f"Bearer {token_a}"},
            json={**payload, "text": "What does the record say about falls?"},
        )
        require(changed.status_code == 409, f"changed text must not reuse turn key: {changed.status_code}")

        cross_person = client.post(
            "/api/v1/conversation/message",
            headers={"Authorization": f"Bearer {token_b}"},
            json={**payload, "person_id": person_b},
        )
        require(cross_person.status_code == 409, f"cross-person replay must be rejected: {cross_person.status_code}")
        require("The saved record does not establish" not in cross_person.text, "cross-person response leaked saved content")

        denied_history = client.get(
            f"/api/v1/conversation/{winner['session_id']}",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        require(denied_history.status_code == 404, f"cross-person history read must be denied: {denied_history.status_code}")
        require("The saved record does not establish" not in denied_history.text, "history denial leaked saved content")

        with TestingSessionLocal() as db:
            rows = (
                db.query(ConversationMessage)
                .filter_by(session_id=winner["session_id"])
                .order_by(ConversationMessage.created_at.asc(), ConversationMessage.id.asc())
                .all()
            )
            require([row.role for row in rows] == ["user", "assistant"], f"expected exactly one persisted turn pair: {[row.role for row in rows]}")
            require(rows[-1].client_turn_id == turn_id, "turn key must be durably stored")
            require(rows[-1].response_metadata_json, "response provenance must be durable")

        print("PASS PostgreSQL Companion concurrent idempotency: one commit, one retryable conflict, one replay")
        print("PASS PostgreSQL Companion changed-text key reuse rejected")
        print("PASS PostgreSQL Companion cross-person replay and history access denied")
        print("PASS PostgreSQL Companion turn order and provenance persisted")
    finally:
        collaboration._ollama_url = original_url
        collaboration._ollama_chat = original_chat
        app.dependency_overrides.clear()
        client.close()
        engine.dispose()


if __name__ == "__main__":
    main()
