"""Regression tests for newly wired collaboration routes and access boundaries."""
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.main import app
from app.models.auth_identity import AuthAccount, AccountPersonAccess
from app.models.care import Person


def _client_for_test():
    app.dependency_overrides.clear()
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    def override_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_db
    client = TestClient(app)
    return engine, Session, client


def _account_context(client, session_factory, email, role="owner"):
    signup = client.post("/api/v1/auth/signup", json={
        "email": email, "password": "a-long-test-password-2026", "display_name": "Test Caregiver"
    })
    assert signup.status_code == 201, signup.text
    login = client.post("/api/v1/auth/login", json={
        "email": email, "password": "a-long-test-password-2026"
    })
    assert login.status_code == 200, login.text
    token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    context = client.post("/api/v1/auth/care-contexts", headers=headers, json={"name": "Test Person"})
    assert context.status_code == 201, context.text
    person_id = context.json()["person_id"]
    if role == "professional":
        with session_factory() as db:
            account = db.query(AuthAccount).filter_by(email=email).one()
            grant = db.query(AccountPersonAccess).filter_by(account_id=account.id, person_id=person_id).one()
            grant.role = "professional"
            db.commit()
    return headers, person_id


def _cleanup(engine, client):
    client.close()
    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


def test_community_post_reaction_and_literal_experience_search_persist():
    engine, Session, client = _client_for_test()
    try:
        headers, person_id = _account_context(client, Session, "community@example.test")
        created = client.post("/api/v1/community", headers=headers, json={
            "caregiver_id": person_id, "content": "Our bedtime routine #sleep helped us settle.",
            "post_type": "experience", "visibility": "public",
        })
        assert created.status_code == 201, created.text
        post_id = created.json()["post"]["id"]
        listed = client.get(f"/api/v1/community?caregiver_id={person_id}", headers=headers)
        assert listed.status_code == 200, listed.text
        assert any(item["id"] == post_id for item in listed.json()["posts"])
        reaction = client.post(f"/api/v1/community/{post_id}/reactions", headers=headers, json={
            "caregiver_id": person_id, "reaction_type": "support"
        })
        assert reaction.status_code == 200, reaction.text
        search = client.post("/api/v1/community/experience", headers=headers, json={"observation": "bedtime routine"})
        assert search.status_code == 200, search.text
        assert any(item["post_id"] == post_id for item in search.json()["matches"])
        # Same reaction toggles off instead of creating duplicate rows.
        client.post(f"/api/v1/community/{post_id}/reactions", headers=headers, json={
            "caregiver_id": person_id, "reaction_type": "support"
        })
        with Session() as db:
            from app.models.collaboration import CommunityReaction, CommunityPost
            assert db.query(CommunityPost).filter_by(id=post_id).count() == 1
            assert db.query(CommunityReaction).filter_by(post_id=post_id).count() == 0
    finally:
        _cleanup(engine, client)


def test_community_cannot_be_written_using_an_unrelated_person_id():
    engine, Session, client = _client_for_test()
    try:
        headers, _person_id = _account_context(client, Session, "community-scope@example.test")
        response = client.post("/api/v1/community", headers=headers, json={
            "caregiver_id": "another-account-person", "content": "A post"
        })
        assert response.status_code == 403, response.text
    finally:
        _cleanup(engine, client)


def test_companion_persists_both_messages_and_does_not_claim_ai_was_used():
    engine, Session, client = _client_for_test()
    try:
        headers, person_id = _account_context(client, Session, "companion@example.test")
        response = client.post("/api/v1/conversation/message", headers=headers, json={
            "person_id": person_id, "text": "Help me organize what happened today."
        })
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["ai_generated"] is False
        assert payload["provider_status"] == "not_configured"
        history = client.get(f"/api/v1/conversation/{payload['session_id']}", headers=headers)
        assert history.status_code == 200, history.text
        assert [item["role"] for item in history.json()["messages"]] == ["user", "assistant"]
    finally:
        _cleanup(engine, client)


def test_companion_rejects_another_accounts_session():
    engine, Session, client = _client_for_test()
    try:
        owner_headers, person_id = _account_context(client, Session, "owner@example.test")
        response = client.post("/api/v1/conversation/message", headers=owner_headers, json={
            "person_id": person_id, "text": "A note"
        })
        session_id = response.json()["session_id"]
        other_headers, _other_person = _account_context(client, Session, "other@example.test")
        history = client.get(f"/api/v1/conversation/{session_id}", headers=other_headers)
        assert history.status_code == 404, history.text
    finally:
        _cleanup(engine, client)


def test_professional_dashboard_requires_explicit_professional_grant():
    engine, Session, client = _client_for_test()
    try:
        headers, _person_id = _account_context(client, Session, "not-professional@example.test")
        response = client.get("/api/v1/professional-dashboard/?timeframe_days=7", headers=headers)
        assert response.status_code == 403, response.text
    finally:
        _cleanup(engine, client)


def test_team_routes_require_staff_role():
    engine, Session, client = _client_for_test()
    try:
        headers, _person_id = _account_context(client, Session, "not-staff@example.test")
        response = client.get("/api/v1/team/dashboard", headers=headers)
        assert response.status_code == 403, response.text
        response = client.get("/api/v1/team/state-report", headers=headers)
        assert response.status_code == 403, response.text
    finally:
        _cleanup(engine, client)
