from fastapi.testclient import TestClient
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.main import app
from app.core.authz import require_person_access
from app.models.auth_identity import AccountPersonAccess, AuthAccount, AuthSession
from app.models.care import Person
from app.models.change import Baseline
from app.models.causal import CausalHypothesis
from app.models.uncertainty import InformationGap


def test_signup_login_current_account_and_logout(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine, tables=[Person.__table__, AuthAccount.__table__, AuthSession.__table__, AccountPersonAccess.__table__, InformationGap.__table__, Baseline.__table__, CausalHypothesis.__table__])
    TestingSession = sessionmaker(bind=engine, expire_on_commit=False)

    def override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setattr("app.api.v1._log_and_learn", lambda *args, **kwargs: None)
    try:
        with TestClient(app) as client:
            signup = client.post("/api/v1/auth/signup", json={
                "email": "caregiver@example.com",
                "password": "a-long-password-2026",
                "display_name": "Caregiver",
            })
            assert signup.status_code == 201, signup.text
            assert signup.json()["email"] == "caregiver@example.com"
            assert "password_hash" not in signup.json()

            duplicate = client.post("/api/v1/auth/signup", json={
                "email": "CAREGIVER@example.com",
                "password": "a-long-password-2026",
            })
            assert duplicate.status_code == 409

            bad_login = client.post("/api/v1/auth/login", json={
                "email": "caregiver@example.com",
                "password": "incorrect-password-2026",
            })
            assert bad_login.status_code == 401

            login = client.post("/api/v1/auth/login", json={
                "email": "caregiver@example.com",
                "password": "a-long-password-2026",
            })
            assert login.status_code == 200, login.text
            token = login.json()["access_token"]
            assert token
            with TestingSession() as db:
                stored = db.query(AuthSession).filter(AuthSession.account_id == signup.json()["id"]).one()
                assert stored.token_digest != token
                assert len(stored.token_digest) == 64

            no_token = client.get("/api/v1/auth/me")
            assert no_token.status_code == 401
            unauthenticated_care_read = client.get("/api/v1/uncertainty/gaps/not-authorized")
            assert unauthenticated_care_read.status_code == 401
            unauthenticated_change_read = client.get("/api/v1/change/baselines/not-authorized")
            assert unauthenticated_change_read.status_code == 401
            unauthenticated_causal_read = client.get("/api/v1/causal/hypotheses/not-authorized")
            assert unauthenticated_causal_read.status_code == 401
            unauthenticated_legacy_read = client.get("/api/v1/persons/not-authorized")
            assert unauthenticated_legacy_read.status_code == 401

            headers = {"Authorization": f"Bearer {token}"}
            current = client.get("/api/v1/auth/me", headers=headers)
            assert current.status_code == 200, current.text
            assert current.json()["email"] == "caregiver@example.com"
            legacy_created = client.post("/api/v1/persons", headers=headers, json={"name": "Legacy Care Recipient"})
            assert legacy_created.status_code == 200, legacy_created.text
            assert client.get("/api/v1/persons/" + legacy_created.json()["id"], headers=headers).status_code == 200

            created_context = client.post(
                "/api/v1/auth/care-contexts",
                headers=headers,
                json={"name": "Care Recipient"},
            )
            assert created_context.status_code == 201, created_context.text
            context = created_context.json()
            assert context["name"] == "Care Recipient"
            assert context["role"] == "owner"
            assert set(context["permissions"]) == {"read", "write", "admin"}
            listed = client.get("/api/v1/auth/care-contexts", headers=headers)
            assert listed.status_code == 200
            assert [item["person_id"] for item in listed.json()] == [context["person_id"]]
            own_legacy_person = client.get("/api/v1/persons/" + context["person_id"], headers=headers)
            assert own_legacy_person.status_code == 200, own_legacy_person.text
            own_gaps = client.get("/api/v1/uncertainty/gaps/" + context["person_id"], headers=headers)
            assert own_gaps.status_code == 200, own_gaps.text
            assert own_gaps.json() == []
            own_baselines = client.get("/api/v1/change/baselines/" + context["person_id"], headers=headers)
            assert own_baselines.status_code == 200, own_baselines.text
            assert own_baselines.json() == []
            own_hypotheses = client.get("/api/v1/causal/hypotheses/" + context["person_id"], headers=headers)
            assert own_hypotheses.status_code == 200, own_hypotheses.text
            assert own_hypotheses.json() == []

            guard = require_person_access("read")
            with TestingSession() as db:
                account = db.query(AuthAccount).filter_by(email="caregiver@example.com").one()
                allowed = guard(person_id=context["person_id"], account=account, db=db)
                assert allowed.id == account.id
                unrelated = Person(name="Unrelated Person")
                db.add(unrelated)
                db.commit()
                db.refresh(unrelated)
                try:
                    guard(person_id=unrelated.id, account=account, db=db)
                    assert False, "Cross-person access should be denied"
                except HTTPException as exc:
                    assert exc.status_code == 403
            unrelated_legacy_person = client.get("/api/v1/persons/" + unrelated.id, headers=headers)
            assert unrelated_legacy_person.status_code == 403, unrelated_legacy_person.text
            unrelated_write = client.post("/api/v1/care-windows", headers=headers, params={"person_id": unrelated.id, "event_type": "test", "occurred_at": "not-a-date"})
            assert unrelated_write.status_code == 403, unrelated_write.text
            unrelated_read = client.get("/api/v1/uncertainty/gaps/" + unrelated.id, headers=headers)
            assert unrelated_read.status_code == 403, unrelated_read.text
            unrelated_baselines = client.get("/api/v1/change/baselines/" + unrelated.id, headers=headers)
            assert unrelated_baselines.status_code == 403, unrelated_baselines.text
            unrelated_hypotheses = client.get("/api/v1/causal/hypotheses/" + unrelated.id, headers=headers)
            assert unrelated_hypotheses.status_code == 403, unrelated_hypotheses.text

            logout = client.post("/api/v1/auth/logout", headers=headers)
            assert logout.status_code == 204
            after_logout = client.get("/api/v1/auth/me", headers=headers)
            assert after_logout.status_code == 401
    finally:
        app.dependency_overrides.pop(get_db, None)
        Base.metadata.drop_all(bind=engine, tables=[CausalHypothesis.__table__, Baseline.__table__, InformationGap.__table__, AccountPersonAccess.__table__, AuthSession.__table__, Person.__table__, AuthAccount.__table__])
        engine.dispose()
