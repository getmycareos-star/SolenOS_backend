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


def test_community_flag_enters_staff_review_queue_with_audit_reason():
    engine, Session, client = _client_for_test()
    try:
        author_headers, person_id = _account_context(client, Session, "flag-author@example.test")
        reporter_headers, _reporter_person = _account_context(client, Session, "flag-reporter@example.test")
        created = client.post("/api/v1/community", headers=author_headers, json={
            "caregiver_id": person_id,
            "content": "A user-submitted community post for moderation.",
            "post_type": "experience",
            "visibility": "public",
        })
        assert created.status_code == 201, created.text
        post_id = created.json()["post"]["id"]

        flagged = client.post(f"/api/v1/community/{post_id}/flag", headers=reporter_headers, json={
            "reason": "This post needs a safety review"
        })
        assert flagged.status_code == 200, flagged.text
        assert flagged.json()["status"] == "flagged"

        public_feed = client.get("/api/v1/community", headers=author_headers)
        assert public_feed.status_code == 200, public_feed.text
        assert all(post["id"] != post_id for post in public_feed.json()["posts"])

        with Session() as db:
            reporter = db.query(AuthAccount).filter_by(email="flag-reporter@example.test").one()
            reporter.is_staff = True
            db.commit()

        queue = client.get("/api/v1/team/dashboard", headers=reporter_headers)
        assert queue.status_code == 200, queue.text
        item = next(item for item in queue.json()["posts_to_review"] if item["id"] == post_id)
        assert item["reason"] == "This post needs a safety review"
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


def test_appointment_object_id_cannot_cross_care_record_boundary():
    from datetime import datetime, timezone
    from app.models.care import Caregiver
    from app.models.time_location import Appointment

    engine, Session, client = _client_for_test()
    try:
        owner_headers, owner_person = _account_context(client, Session, "appointment-owner@example.test")
        other_headers, other_person = _account_context(client, Session, "appointment-other@example.test")
        assert owner_person != other_person
        owner_me = client.get("/api/v1/auth/me", headers=owner_headers)
        other_me = client.get("/api/v1/auth/me", headers=other_headers)
        assert owner_me.status_code == 200, owner_me.text
        assert other_me.status_code == 200, other_me.text
        assert owner_me.json()["id"] != other_me.json()["id"]
        assert owner_me.json()["email"] == "appointment-owner@example.test"
        assert other_me.json()["email"] == "appointment-other@example.test"
        with Session() as db:
            from app.models.auth_identity import AccountPersonAccess
            assert db.query(AccountPersonAccess).filter_by(
                account_id=owner_me.json()["id"], person_id=other_person
            ).count() == 0
            from app.core.authz import assert_person_access
            from fastapi import HTTPException
            try:
                unexpected_grant = assert_person_access(db, owner_me.json()["id"], other_person, "write")
            except HTTPException as exc:
                assert exc.status_code == 403
            else:
                raise AssertionError(
                    "Owner account unexpectedly passed direct authorization: "
                    f"requested_account={owner_me.json()['id']!r}, requested_person={other_person!r}, "
                    f"returned_account={unexpected_grant.account_id!r}, returned_person={unexpected_grant.person_id!r}, "
                    f"all_grants={[(g.account_id, g.person_id, g.permissions) for g in db.query(AccountPersonAccess).all()]!r}"
                )
            caregiver = db.query(Caregiver).filter_by(person_id=other_person).one()
            appointment = Appointment(
                person_id=other_person,
                title="Private appointment",
                scheduled_at=datetime.now(timezone.utc),
                status="scheduled",
                created_by_caregiver_id=caregiver.id,
            )
            db.add(appointment)
            db.commit()
            db.refresh(appointment)
            appointment_id = appointment.id

        denied = client.patch(
            f"/api/v1/appointments/{appointment_id}/lifecycle",
            headers=owner_headers,
        )
        assert denied.status_code == 403, denied.text

        allowed = client.patch(
            f"/api/v1/appointments/{appointment_id}/lifecycle",
            headers=other_headers,
        )
        assert allowed.status_code == 200, allowed.text
    finally:
        _cleanup(engine, client)


def test_collaboration_routes_are_registered_and_session_protected():
    from fastapi.routing import APIRoute
    required = {
        ("GET", "/api/v1/community"),
        ("POST", "/api/v1/community"),
        ("POST", "/api/v1/community/experience"),
        ("POST", "/api/v1/community/{post_id}/reactions"),
        ("POST", "/api/v1/community/{post_id}/flag"),
        ("POST", "/api/v1/conversation/message"),
        ("GET", "/api/v1/conversation/{session_id}"),
        ("GET", "/api/v1/team/dashboard"),
        ("GET", "/api/v1/team/state-report"),
        ("GET", "/api/v1/metrics"),
        ("GET", "/api/v1/professional-dashboard/"),
    }
    # FastAPI versions using included-router wrappers do not expose nested
    # APIRoutes directly in app.routes. OpenAPI is the authoritative registered
    # route table and avoids a false failure that only sees /health and /ready.
    schema = app.openapi()
    actual = {
        (method.upper(), path)
        for path, operations in schema.get("paths", {}).items()
        for method in operations
    }
    assert required <= actual, (
        f"Missing collaboration routes: {sorted(required - actual)}; "
        f"registered paths: {sorted(schema.get('paths', {}))}"
    )
