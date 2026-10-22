from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.main import app
from app.services import conversation_service


engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(
    autocommit=False, autoflush=False, bind=engine, expire_on_commit=False
)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


def setup_db():
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = override_get_db


def teardown_db():
    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=engine)


def create_owner(client: TestClient):
    email = "companion-owner@example.com"
    password = "a-long-test-password-2026"
    assert client.post(
        "/api/v1/auth/signup",
        json={"email": email, "password": password, "display_name": "Companion Owner"},
    ).status_code == 201
    login = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert login.status_code == 200, login.text
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    context = client.post(
        "/api/v1/auth/care-contexts", headers=headers, json={"name": "Companion Test Record"}
    )
    assert context.status_code == 201, context.text
    return headers, context.json()["person_id"]


def test_companion_endpoint_saves_messages_and_returns_explicit_provider_state(monkeypatch):
    setup_db()
    try:
        monkeypatch.setattr(
            conversation_service,
            "get_settings",
            lambda: SimpleNamespace(
                ollama_configured=False,
                ollama_base_url="",
                ollama_model="",
                ollama_timeout_seconds=20,
            ),
        )
        with TestClient(app) as client:
            headers, person_id = create_owner(client)
            response = client.post(
                "/api/v1/conversation/message",
                headers=headers,
                json={"person_id": person_id, "text": "What should I keep track of today?"},
            )
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["session_id"]
            assert isinstance(body["reply"], str) and body["reply"]
            assert body["provider_status"] == "not_configured"
            assert body["ai_generated"] is False

            history = client.get(
                f"/api/v1/conversation/{body['session_id']}", headers=headers
            )
            assert history.status_code == 200, history.text
            rows = history.json()["messages"]
            assert [row["role"] for row in rows] == ["user", "assistant"]
            assert rows[0]["text"] == "What should I keep track of today?"
    finally:
        teardown_db()


def test_companion_cannot_read_another_account_care_context(monkeypatch):
    setup_db()
    try:
        monkeypatch.setattr(
            conversation_service,
            "get_settings",
            lambda: SimpleNamespace(
                ollama_configured=False,
                ollama_base_url="",
                ollama_model="",
                ollama_timeout_seconds=20,
            ),
        )
        with TestClient(app) as client:
            headers, _ = create_owner(client)
            response = client.post(
                "/api/v1/conversation/message",
                headers=headers,
                json={"person_id": "unrelated-care-record", "text": "Summarize the record"},
            )
            assert response.status_code == 403
    finally:
        teardown_db()
