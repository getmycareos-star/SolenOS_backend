from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db, SessionLocal, engine
from app.main import app
from app.models.care import Person, Caregiver
from app.models.collaboration import CommunityPost, ConversationMessage
from app.models.auth import AuthAccount
import uuid
from app.api.auth import _hash_password, _verify_password, issue_token, verify_token, settings

engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


def seed():
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    p1 = Person(id="collab-person-1", name="Care Recipient One")
    p2 = Person(id="collab-person-2", name="Care Recipient Two")
    c1 = Caregiver(id="collab-caregiver-1", name="Caregiver One", email="one@example.com", relationship="Family", person_id=p1.id)
    c2 = Caregiver(id="collab-caregiver-2", name="Caregiver Two", email="two@example.com", relationship="Family", person_id=p2.id)
    a1 = AuthAccount(caregiver_id=c1.id, email=c1.email, password_hash="test-hash", role="caregiver", is_active=True)
    a2 = AuthAccount(caregiver_id=c2.id, email=c2.email, password_hash="test-hash", role="professional", is_active=True)
    db.add_all([p1, p2, c1, c2, a1, a2]); db.commit(); db.close()
    app.dependency_overrides[get_db] = override_db


def cleanup():
    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=engine)


def test_community_post_requires_moderation_then_can_be_reacted_to():
    seed()
    try:
        client = TestClient(app)
        created = client.post("/api/v1/community", json={
            "caregiver_id": "collab-caregiver-1", "author_type": "peer_caregiver",
            "author_label": "Anonymous caregiver", "post_type": "experience",
            "content": "A quiet evening routine helped us prepare for bedtime.",
            "tags": ["routine", "sleep"], "visibility": "public",
        })
        assert created.status_code == 201, created.text
        post = created.json()
        assert post["moderation_status"] == "pending"
        assert client.get("/api/v1/community").json()["count"] == 0

        published = client.patch("/api/v1/team/moderation/" + post["id"], json={"moderation_status": "published"})
        assert published.status_code == 200, published.text
        feed = client.get("/api/v1/community?caregiver_id=collab-caregiver-1")
        assert feed.status_code == 200
        assert feed.json()["count"] == 1
        assert feed.json()["posts"][0]["content"] == post["content"]

        reaction = client.post("/api/v1/community/" + post["id"] + "/reactions", json={
            "caregiver_id": "collab-caregiver-1", "reaction_type": "support",
        })
        assert reaction.status_code == 200, reaction.text
        assert reaction.json()["reaction_counts"]["support"] == 1
        assert reaction.json()["user_reaction"] == "support"
    finally:
        cleanup()


def test_companion_messages_persist_and_cannot_cross_care_recipients():
    seed()
    try:
        client = TestClient(app)
        response = client.post("/api/v1/conversation/message", json={
            "person_id": "collab-person-1", "caregiver_id": "collab-caregiver-1",
            "text": "What do we have recorded about care today?",
        })
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["session_id"]
        assert data["person_id"] == "collab-person-1"
        assert data["reply"]
        assert len(data["messages"]) == 2

        db = TestingSessionLocal()
        rows = db.query(ConversationMessage).filter_by(session_id=data["session_id"]).all()
        assert len(rows) == 2
        assert [row.role for row in rows] == ["user", "assistant"]
        db.close()

        denied = client.post("/api/v1/conversation/message", json={
            "person_id": "collab-person-2", "caregiver_id": "collab-caregiver-1",
            "text": "Try to cross into another record.",
        })
        assert denied.status_code == 403
    finally:
        cleanup()


def test_professional_dashboard_only_lists_active_grants():
    seed()
    try:
        client = TestClient(app)
        granted = client.post("/api/v1/professional/access-grants", json={
            "professional_caregiver_id": "collab-caregiver-2",
            "person_id": "collab-person-1",
            "granted_by_caregiver_id": "collab-caregiver-1",
            "organization": "Test clinic",
            "status": "active",
        })
        assert granted.status_code == 200, granted.text
        response = client.get("/api/v1/professional/dashboard?professional_caregiver_id=collab-caregiver-2")
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["count"] == 1
        assert data["care_recipients"][0]["person"]["id"] == "collab-person-1"
    finally:
        cleanup()


def test_team_dashboard_metrics_are_database_aggregates():
    seed()
    try:
        client = TestClient(app)
        response = client.get("/api/v1/team/dashboard")
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["people_count"] == 2
        assert data["caregiver_count"] == 2
        assert data["data_source"] == "database aggregates"
        assert client.get("/api/v1/metrics").status_code == 200
        assert client.get("/api/v1/team/state-report").status_code == 200
    finally:
        cleanup()


def test_password_hash_and_signed_token_reject_tampering(monkeypatch):
    monkeypatch.setattr(settings, "secret_key", "s" * 64)
    encoded = _hash_password("correct horse battery staple")
    assert _verify_password("correct horse battery staple", encoded)
    assert not _verify_password("wrong password", encoded)

    class Account:
        id = "auth-test-account"
        caregiver_id = "auth-test-caregiver"
        role = "caregiver"

    token = issue_token(Account())
    claims = verify_token(token)
    assert claims["sub"] == "auth-test-account"
    assert claims["role"] == "caregiver"
    try:
        verify_token(token + "tampered")
        assert False, "tampered token unexpectedly accepted"
    except Exception:
        pass


def test_register_and_login_issue_backend_session(monkeypatch):
    seed()
    try:
        monkeypatch.setenv("ACCOUNT_ENROLLMENT_KEY", "enrollment-key-for-tests-long-enough")
        monkeypatch.setattr(settings, "secret_key", "s" * 64)
        client = TestClient(app)
        response = client.post("/api/v1/auth/register", json={
            "email": "one@example.com",
            "password": "correct horse battery staple",
            "enrollment_key": "enrollment-key-for-tests-long-enough",
            "caregiver_id": "collab-caregiver-1",
            "person_id": "collab-person-1",
        })
        assert response.status_code == 201, response.text
        assert response.json()["account"]["person_id"] == "collab-person-1"
        login = client.post("/api/v1/auth/login", json={
            "email": "one@example.com", "password": "correct horse battery staple",
        })
        assert login.status_code == 200, login.text
        assert login.json()["token_type"] == "bearer"
        assert login.json()["account"]["caregiver_id"] == "collab-caregiver-1"
    finally:
        cleanup()


def test_auth_middleware_requires_token_and_isolates_care_recipients(monkeypatch):
    # Exercise the real middleware and real configured SQLAlchemy session; do not
    # override get_db because the app intentionally bypasses auth only for isolated
    # dependency-overridden unit tests.
    app.dependency_overrides.clear()
    monkeypatch.setattr(settings, "secret_key", "m" * 64)
    Base.metadata.create_all(bind=engine)
    suffix = str(uuid.uuid4())
    person_id = "auth-person-" + suffix
    other_person_id = "auth-other-person-" + suffix
    caregiver_id = "auth-caregiver-" + suffix
    account_id = "auth-account-" + suffix
    db = SessionLocal()
    try:
        person = Person(id=person_id, name="Authorized Person")
        other_person = Person(id=other_person_id, name="Other Person")
        caregiver = Caregiver(id=caregiver_id, name="Authorized Caregiver", email="auth-" + suffix + "@example.com", relationship="Family", person_id=person_id)
        account = AuthAccount(id=account_id, caregiver_id=caregiver_id, email=caregiver.email, password_hash="unused", role="caregiver", is_active=True)
        db.add_all([person, other_person, caregiver, account])
        db.commit()

        token = issue_token(account)
        client = TestClient(app)
        missing = client.get("/api/v1/persons/" + person_id)
        assert missing.status_code == 401

        allowed = client.get("/api/v1/persons/" + person_id, headers={"Authorization": "Bearer " + token})
        assert allowed.status_code == 200, allowed.text
        assert allowed.json()["id"] == person_id

        denied = client.get("/api/v1/persons/" + other_person_id, headers={"Authorization": "Bearer " + token})
        assert denied.status_code == 403, denied.text
    finally:
        db.query(AuthAccount).filter_by(id=account_id).delete(synchronize_session=False)
        db.query(Caregiver).filter_by(id=caregiver_id).delete(synchronize_session=False)
        db.query(Person).filter(Person.id.in_([person_id, other_person_id])).delete(synchronize_session=False)
        db.commit()
        db.close()
        app.dependency_overrides.clear()
