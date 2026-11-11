"""Regression coverage for anonymous-session creation audit events."""
import hashlib

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.main as main_module
from app.core.database import Base, get_db, SessionLocal as DefaultSessionLocal
from app.main import app


engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_get_db():
    db = TestSessionLocal()
    try:
        yield db
    finally:
        db.close()


def test_anonymous_session_creation_is_audited_without_recording_bearer_token():
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = override_get_db
    main_module.SessionLocal = TestSessionLocal
    try:
        client = TestClient(app)
        response = client.post(
            "/api/v1/sessions/anonymous",
            json={"name": "Audit recipient", "caregiver_name": "Audit caregiver"},
        )
        assert response.status_code == 201, response.text
        body = response.json()

        from app.models.anonymous_session import AnonymousSession
        from app.models.privacy_delivery import AuditLogEvent

        db = TestSessionLocal()
        try:
            session = db.query(AnonymousSession).filter_by(
                token_hash=hashlib.sha256(body["access_token"].encode()).hexdigest()
            ).one()
            event = db.query(AuditLogEvent).filter_by(
                action="anonymous_session.created",
                target_type="anonymous_session",
                target_id=session.id,
            ).one()

            assert event.actor_id == body["caregiver"]["id"]
            assert event.subject_id == body["person"]["id"]
            assert event.person_id == body["person"]["id"]
            assert event.caregiver_id == body["caregiver"]["id"]
            assert event.outcome == "success"
            assert event.policy_version == "anonymous-session-lifecycle-v1"
            assert event.record_metadata == {
                "expires_at": body["expires_at"],
                "token_storage": "sha256_digest_only",
            }
            assert body["access_token"] not in str(event.record_metadata)
            assert body["access_token"] not in session.token_hash
        finally:
            db.close()
    finally:
        app.dependency_overrides.clear()
        main_module.SessionLocal = DefaultSessionLocal
        Base.metadata.drop_all(bind=engine)
        engine.dispose()
