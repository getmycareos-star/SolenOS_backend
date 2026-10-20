from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.main import app
from app.models.auth_identity import AuthAccount, AuthSession


def test_signup_login_current_account_and_logout():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine, tables=[AuthAccount.__table__, AuthSession.__table__])
    TestingSession = sessionmaker(bind=engine, expire_on_commit=False)

    def override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
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

            headers = {"Authorization": f"Bearer {token}"}
            current = client.get("/api/v1/auth/me", headers=headers)
            assert current.status_code == 200, current.text
            assert current.json()["email"] == "caregiver@example.com"

            logout = client.post("/api/v1/auth/logout", headers=headers)
            assert logout.status_code == 204
            after_logout = client.get("/api/v1/auth/me", headers=headers)
            assert after_logout.status_code == 401
    finally:
        app.dependency_overrides.pop(get_db, None)
        Base.metadata.drop_all(bind=engine, tables=[AuthSession.__table__, AuthAccount.__table__])
        engine.dispose()
