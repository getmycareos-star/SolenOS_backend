from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.main import app
from app.models.care import CareEvent, Caregiver


def make_environment():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    testing_session = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_db():
        db = testing_session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    signup = client.post(
        "/api/v1/auth/signup",
        json={
            "email": "daily-capture-test@example.invalid",
            "password": "daily-capture-test-password",
            "display_name": "Daily Capture Tester",
        },
    )
    assert signup.status_code == 201, signup.text
    login = client.post(
        "/api/v1/auth/login",
        json={
            "email": "daily-capture-test@example.invalid",
            "password": "daily-capture-test-password",
        },
    )
    assert login.status_code == 200, login.text
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    context_response = client.post(
        "/api/v1/auth/care-contexts",
        headers=headers,
        json={"name": "Daily Capture Test Person"},
    )
    assert context_response.status_code == 201, context_response.text
    person_id = context_response.json()["person_id"]
    caregiver_response = client.get(f"/api/v1/caregivers/{person_id}", headers=headers)
    assert caregiver_response.status_code == 200, caregiver_response.text
    caregiver_id = caregiver_response.json()[0]["id"]

    with testing_session() as db:
        unlinked = Caregiver(
            name="Unlinked Caregiver",
            email="unlinked-daily-capture@example.invalid",
            relationship="Family",
            person_id=person_id,
        )
        db.add(unlinked)
        db.commit()
        db.refresh(unlinked)
        unlinked_caregiver_id = unlinked.id

    return engine, testing_session, client, headers, person_id, caregiver_id, unlinked_caregiver_id


def test_daily_care_entry_persists_with_server_verified_caregiver_attribution():
    engine, testing_session, client, headers, person_id, caregiver_id, _ = make_environment()
    try:
        response = client.post(
            "/api/v1/daily-care/entries",
            headers=headers,
            json={
                "person_id": person_id,
                "caregiver_id": caregiver_id,
                "mood": "Calm",
                "general_notes": "User-recorded note",
                "created_by_caregiver_id": "forged-caregiver-id",
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["success"] is True

        with testing_session() as db:
            event = db.query(CareEvent).filter_by(id=response.json()["care_event_id"]).one()
            assert event.person_id == person_id
            assert event.created_by_caregiver_id == caregiver_id
            assert "User-recorded note" in event.description
            assert "forged-caregiver-id" not in event.description
    finally:
        client.close()
        app.dependency_overrides.clear()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_daily_care_entry_rejects_missing_authentication():
    engine, _, _, _, person_id, caregiver_id, _ = make_environment()
    # Use a separate client with an empty cookie jar and no default headers so
    # this is an actual unauthenticated request, not a reused authenticated session.
    unauthenticated_client = TestClient(app)
    try:
        response = unauthenticated_client.post(
            "/api/v1/daily-care/entries",
            headers={"Authorization": ""},
            json={"person_id": person_id, "caregiver_id": caregiver_id, "mood": "Calm"},
        )
        assert response.status_code == 401, response.text
    finally:
        unauthenticated_client.close()
        app.dependency_overrides.clear()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_daily_care_entry_rejects_unlinked_caregiver_identity():
    engine, _, client, headers, person_id, _, unlinked_caregiver_id = make_environment()
    try:
        response = client.post(
            "/api/v1/daily-care/entries",
            headers=headers,
            json={
                "person_id": person_id,
                "caregiver_id": unlinked_caregiver_id,
                "mood": "Calm",
            },
        )
        assert response.status_code == 403, response.text
    finally:
        client.close()
        app.dependency_overrides.clear()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_daily_care_entry_rejects_a_care_record_without_access():
    engine, _, client, headers, _, caregiver_id, _ = make_environment()
    try:
        response = client.post(
            "/api/v1/daily-care/entries",
            headers=headers,
            json={
                "person_id": "another-account-care-record",
                "caregiver_id": caregiver_id,
                "mood": "Calm",
            },
        )
        assert response.status_code == 403, response.text
    finally:
        client.close()
        app.dependency_overrides.clear()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()
