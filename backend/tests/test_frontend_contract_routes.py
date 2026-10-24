import hashlib
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.main as main_module
from app.core.database import Base, get_db
from app.main import app
from app.models.collaboration import CommunityPost, ConversationMessage
from app.models.care import Caregiver, Person
from app.models.anonymous_session import AnonymousSession

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


def setup_client(monkeypatch):
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = override_db
    monkeypatch.setattr(main_module, "SessionLocal", TestingSessionLocal)
    db = TestingSessionLocal()
    person = Person(id="contract-person-1", name="Care recipient")
    caregiver = Caregiver(
        id="contract-caregiver-1",
        name="Family caregiver",
        email=None,
        relationship="Family",
        person_id=person.id,
    )
    other_person = Person(id="contract-person-2", name="Other care recipient")
    other_caregiver = Caregiver(
        id="contract-caregiver-2",
        name="Other caregiver",
        email=None,
        relationship="Family",
        person_id=other_person.id,
    )
    db.add_all([person, caregiver, other_person, other_caregiver])
    token = "contract-test-token"
    db.add(AnonymousSession(
        id="contract-session-1",
        person_id=person.id,
        caregiver_id=caregiver.id,
        token_hash=hashlib.sha256(token.encode()).hexdigest(),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    ))
    other_token = "contract-test-token-2"
    db.add(AnonymousSession(
        id="contract-session-2",
        person_id=other_person.id,
        caregiver_id=other_caregiver.id,
        token_hash=hashlib.sha256(other_token.encode()).hexdigest(),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    ))
    db.commit()
    db.close()
    return TestClient(app), token, other_token


def teardown():
    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=engine)


def test_community_post_feed_reaction_persist_and_session_attribution(monkeypatch):
    client, token, other_token = setup_client(monkeypatch)
    try:
        auth = {"Authorization": f"Bearer {token}"}
        created = client.post("/api/v1/community", headers=auth, json={
            "caregiver_id": "caller-controlled-value",
            "author_type": "solenos_verified",
            "author_label": "Forged verified identity",
            "post_type": "experience",
            "content": "A quiet routine helped us prepare for bedtime.",
            "tags": ["routine", "sleep"],
            "visibility": "public",
        })
        # Middleware rejects caller-supplied caregiver attribution that differs
        # from the caregiver bound to the bearer session.
        assert created.status_code == 403

        created = client.post("/api/v1/community", headers=auth, json={
            "post_type": "experience",
            "content": "A quiet routine helped us prepare for bedtime.",
            "tags": ["routine", "sleep"],
        })
        assert created.status_code == 201, created.text
        post = created.json()
        assert post["author_type"] == "peer_caregiver"
        assert post["author_label"] == "Anonymous caregiver"

        feed = client.get("/api/v1/community", headers=auth)
        assert feed.status_code == 200
        assert any(row["id"] == post["id"] for row in feed.json()["posts"])

        reaction = client.post(
            f"/api/v1/community/{post['id']}/reactions",
            headers=auth,
            json={"reaction_type": "support"},
        )
        assert reaction.status_code == 200, reaction.text
        assert reaction.json()["reaction_counts"]["support"] == 1

        # Reactions are attributed to the server-derived caregiver, not a body ID.
        other = client.post(
            f"/api/v1/community/{post['id']}/reactions",
            headers={"Authorization": f"Bearer {other_token}"},
            json={"reaction_type": "like"},
        )
        assert other.status_code == 200, other.text
        db = TestingSessionLocal()
        assert db.query(CommunityPost).filter_by(id=post["id"]).count() == 1
        db.close()
    finally:
        teardown()


def test_companion_emergency_response_is_scoped_and_persisted_without_model(monkeypatch):
    client, token, other_token = setup_client(monkeypatch)
    try:
        auth = {"Authorization": f"Bearer {token}"}
        denied = client.post("/api/v1/conversation/message", headers=auth, json={
            "person_id": "contract-person-2",
            "text": "What happened today?",
        })
        assert denied.status_code == 403

        response = client.post("/api/v1/conversation/message", headers=auth, json={
            "person_id": "contract-person-1",
            "text": "They are unconscious and not breathing",
        })
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["guard_verdict"] == "blocked"
        assert "urgent medical help" in data["reply"].lower()
        assert data["ai_generated"] is False
        assert len(data["messages"]) == 2

        history = client.get(f"/api/v1/conversation/{data['session_id']}", headers=auth)
        assert history.status_code == 200
        assert len(history.json()["messages"]) == 2

        cross_record = client.get(
            f"/api/v1/conversation/{data['session_id']}",
            headers={"Authorization": f"Bearer {other_token}"},
        )
        assert cross_record.status_code == 404

        db = TestingSessionLocal()
        rows = db.query(ConversationMessage).filter_by(session_id=data["session_id"]).all()
        assert [row.role for row in rows] == ["user", "assistant"]
        db.close()
    finally:
        teardown()
