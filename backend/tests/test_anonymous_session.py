"""Anonymous first-use sessions create and refresh one server-authorized empty care record."""
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.main import app
from app.models.auth_identity import AccountPersonAccess, AuthAccount, CaregiverAccountLink
from app.models.care import Caregiver, Person
from app.services.auth_service import resolve_session

engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine, expire_on_commit=False)


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
    app.dependency_overrides.pop(get_db, None)
    Base.metadata.drop_all(bind=engine)


def test_anonymous_session_creates_real_empty_record_and_refreshes_same_identity():
    setup_db()
    try:
        with TestClient(app) as client:
            created = client.post("/api/v1/sessions/anonymous", json={"caregiver_name": "Caregiver"})
            assert created.status_code == 201, created.text
            first = created.json()
            assert first["access_token"]
            assert first["token_type"] == "Bearer"
            assert first["session_id"]
            assert first["person"]["name"] == "Unnamed care recipient"
            assert first["caregiver"]["id"]
            assert first["expires_in_seconds"] > 24 * 60 * 60

            with TestingSessionLocal() as db:
                account = resolve_session(db, first["access_token"])
                assert account is not None
                assert account.is_anonymous is True
                assert db.query(AccountPersonAccess).filter_by(
                    account_id=account.id, person_id=first["person"]["id"], is_active=True
                ).count() == 1
                assert db.query(CaregiverAccountLink).filter_by(
                    account_id=account.id, caregiver_id=first["caregiver"]["id"], is_active=True
                ).count() == 1
                assert db.query(Person).filter_by(id=first["person"]["id"]).count() == 1
                assert db.query(Caregiver).filter_by(id=first["caregiver"]["id"]).count() == 1

            old_headers = {"Authorization": "Bearer " + first["access_token"]}
            events = client.get(f"/api/v1/events/{first['person']['id']}", headers=old_headers)
            evidence = client.get(f"/api/v1/evidence/{first['person']['id']}", headers=old_headers)
            assert events.status_code == 200, events.text
            assert events.json() == []
            assert evidence.status_code == 200, evidence.text
            assert evidence.json() == []

            refreshed = client.post("/api/v1/sessions/refresh", headers=old_headers)
            assert refreshed.status_code == 200, refreshed.text
            second = refreshed.json()
            assert second["access_token"] != first["access_token"]
            assert second["person"]["id"] == first["person"]["id"]
            assert second["caregiver"]["id"] == first["caregiver"]["id"]
            assert second["session_id"] != first["session_id"]

            old_token_result = client.get(f"/api/v1/events/{first['person']['id']}", headers=old_headers)
            assert old_token_result.status_code == 401, old_token_result.text
            new_token_result = client.get(
                f"/api/v1/events/{second['person']['id']}",
                headers={"Authorization": "Bearer " + second["access_token"]},
            )
            assert new_token_result.status_code == 200, new_token_result.text
    finally:
        teardown_db()


def test_anonymous_refresh_rejects_regular_account_sessions():
    setup_db()
    try:
        with TestClient(app) as client:
            signup = client.post("/api/v1/auth/signup", json={
                "email": "regular-refresh@example.com",
                "password": "a-long-test-password-2026",
                "display_name": "Regular Account",
            })
            assert signup.status_code == 201, signup.text
            login = client.post("/api/v1/auth/login", json={
                "email": "regular-refresh@example.com",
                "password": "a-long-test-password-2026",
            })
            assert login.status_code == 200, login.text
            response = client.post(
                "/api/v1/sessions/refresh",
                headers={"Authorization": "Bearer " + login.json()["access_token"]},
            )
            assert response.status_code == 403, response.text
    finally:
        teardown_db()



def test_anonymous_caregiver_can_use_community_without_writing_to_the_care_record():
    setup_db()
    try:
        with TestClient(app) as client:
            session = client.post("/api/v1/sessions/anonymous", json={"caregiver_name": "Caregiver"})
            assert session.status_code == 201, session.text
            body = session.json()
            headers = {"Authorization": "Bearer " + body["access_token"]}
            caregiver_id = body["caregiver"]["id"]
            person_id = body["person"]["id"]

            created = client.post("/api/v1/community", headers=headers, json={
                "caregiver_id": caregiver_id,
                "content": "A peer experience shared by this caregiver.",
                "post_type": "experience",
                "visibility": "public",
                "care_stage": "unspecified",
            })
            assert created.status_code == 201, created.text
            post_id = created.json()["post"]["id"]

            listed = client.get(
                "/api/v1/community",
                params={"caregiver_id": caregiver_id},
                headers=headers,
            )
            assert listed.status_code == 200, listed.text
            assert any(item["id"] == post_id for item in listed.json()["posts"])

            reaction = client.post(
                f"/api/v1/community/{post_id}/reactions",
                headers=headers,
                json={"caregiver_id": caregiver_id, "reaction_type": "support"},
            )
            assert reaction.status_code == 200, reaction.text

            events = client.get(f"/api/v1/events/{person_id}", headers=headers)
            evidence = client.get(f"/api/v1/evidence/{person_id}", headers=headers)
            assert events.status_code == 200, events.text
            assert evidence.status_code == 200, evidence.text
            assert events.json() == []
            assert evidence.json() == []
    finally:
        teardown_db()
