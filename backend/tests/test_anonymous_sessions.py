from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.main as main_module
from app.core.database import Base, get_db
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


def setup_function():
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = override_get_db
    main_module.SessionLocal = TestSessionLocal


def teardown_function():
    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=engine)


def create_session(client, person_name):
    response = client.post(
        "/api/v1/sessions/anonymous",
        json={"name": person_name, "caregiver_name": "Anonymous caregiver"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["person"]["id"]
    assert body["caregiver"]["id"]
    assert body["access_token"]
    return body


def test_anonymous_session_bootstrap_creates_scoped_record_without_email():
    client = TestClient(app)
    created = create_session(client, "Care recipient")
    assert created["token_type"] == "Bearer"

    from app.models.care import Caregiver, Person
    from app.models.anonymous_session import AnonymousSession
    import hashlib

    db = TestSessionLocal()
    try:
        caregiver = db.query(Caregiver).filter_by(id=created["caregiver"]["id"]).one()
        session = db.query(AnonymousSession).filter_by(
            token_hash=hashlib.sha256(created["access_token"].encode()).hexdigest()
        ).one()
        person = db.query(Person).filter_by(id=created["person"]["id"]).one()
        assert caregiver.email is None
        assert caregiver.person_id == person.id
        assert session.person_id == person.id
        assert session.caregiver_id == caregiver.id
        assert created["access_token"] not in session.token_hash
    finally:
        db.close()


def test_care_data_requires_server_issued_bearer_token():
    client = TestClient(app)
    created = create_session(client, "Care recipient")
    response = client.get(f"/api/v1/evidence/{created['person']['id']}")
    assert response.status_code == 401, response.text


def test_session_cannot_read_another_person_evidence():
    client = TestClient(app)
    first = create_session(client, "First person")
    second = create_session(client, "Second person")
    response = client.get(
        f"/api/v1/evidence/{first['person']['id']}",
        headers={"Authorization": f"Bearer {second['access_token']}"},
    )
    assert response.status_code == 403, response.text


def test_session_cannot_spoof_another_caregiver_attribution():
    client = TestClient(app)
    first = create_session(client, "First person")
    second = create_session(client, "Second person")
    response = client.post(
        "/api/v1/daily-care/entries",
        headers={"Authorization": f"Bearer {first['access_token']}"},
        json={
            "person_id": first["person"]["id"],
            "caregiver_id": second["caregiver"]["id"],
            "mood": "Calm",
        },
    )
    assert response.status_code == 403, response.text
