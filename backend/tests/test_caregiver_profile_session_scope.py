import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.main as main_module
from app.core.database import Base, get_db, SessionLocal as DefaultSessionLocal
from app.main import app
from app.models.care import Caregiver, Person
from app.models.caregiver_intelligence import CaregiverProfile, CaregiverObservation, CaregiverHandoff
from app.core.caregiver_enums import InformationVisibility

pytestmark = pytest.mark.session_security

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
    main_module.SessionLocal = DefaultSessionLocal
    Base.metadata.drop_all(bind=engine)


def create_session(client, name="Care recipient"):
    response = client.post(
        "/api/v1/sessions/anonymous",
        json={"name": name, "caregiver_name": "Session caregiver"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def create_second_profile(person_id: str, *, name="Other caregiver", email=None):
    db = TestSessionLocal()
    try:
        caregiver = Caregiver(
            name=name,
            email=None,
            relationship="Family",
            person_id=person_id,
        )
        db.add(caregiver)
        db.flush()
        profile = CaregiverProfile(
            person_id=person_id,
            session_caregiver_id=caregiver.id,
            name=name,
            email=email,
            relationship="Family",
        )
        db.add(profile)
        db.commit()
        db.refresh(profile)
        return profile.id, caregiver.id
    finally:
        db.close()


def test_anonymous_session_cannot_read_another_caregivers_private_observation():
    client = TestClient(app)
    session = create_session(client)
    person_id = session["person"]["id"]
    own_profile_id = session["caregiver"]["profile_id"]
    other_profile_id, _other_session_caregiver_id = create_second_profile(person_id)

    db = TestSessionLocal()
    try:
        observation = CaregiverObservation(
            person_id=person_id,
            caregiver_id=other_profile_id,
            observation_content="Private note from another caregiver",
            information_visibility=InformationVisibility.PRIVATE,
            is_promoted_to_shared=False,
        )
        db.add(observation)
        db.commit()
        db.refresh(observation)
        observation_id = observation.id
    finally:
        db.close()

    headers = {"Authorization": f"Bearer {session['access_token']}"}
    filtered = client.get(
        f"/api/v1/caregiver-observations/{person_id}",
        params={"caregiver_id": other_profile_id},
        headers=headers,
    )
    assert filtered.status_code == 200, filtered.text
    assert filtered.json() == []

    direct = client.get(
        f"/api/v1/caregiver-observations/{person_id}/caregiver/{other_profile_id}",
        headers=headers,
    )
    assert direct.status_code == 403, direct.text

    promote = client.post(
        f"/api/v1/caregiver-observations/{observation_id}/promote",
        params={"caregiver_id": own_profile_id},
        headers=headers,
    )
    assert promote.status_code == 404, promote.text

    db = TestSessionLocal()
    try:
        persisted = db.query(CaregiverObservation).filter_by(id=observation_id).one()
        assert persisted.is_promoted_to_shared is False
        assert persisted.information_visibility == InformationVisibility.PRIVATE
    finally:
        db.close()


def test_handoff_acceptance_is_limited_to_the_intended_recipient():
    client = TestClient(app)
    session = create_session(client)
    person_id = session["person"]["id"]
    other_profile_id, _ = create_second_profile(person_id)

    db = TestSessionLocal()
    try:
        handoff = CaregiverHandoff(
            person_id=person_id,
            from_caregiver_id=other_profile_id,
            to_caregiver_id=other_profile_id,
            responsibility_type="appointment",
        )
        db.add(handoff)
        db.commit()
        db.refresh(handoff)
        handoff_id = handoff.id
    finally:
        db.close()

    response = client.post(
        f"/api/v1/caregiver-handoffs/{handoff_id}/accept",
        headers={"Authorization": f"Bearer {session['access_token']}"},
        params={"acceptance_evidence": "accepted"},
    )
    assert response.status_code == 404, response.text

    db = TestSessionLocal()
    try:
        persisted = db.query(CaregiverHandoff).filter_by(id=handoff_id).one()
        assert persisted.is_completed is False
    finally:
        db.close()


def test_caregiver_profile_writes_cannot_change_another_profiles_involvement():
    client = TestClient(app)
    session = create_session(client)
    person_id = session["person"]["id"]
    other_profile_id, _ = create_second_profile(person_id)
    response = client.patch(
        f"/api/v1/caregiver-profiles/{other_profile_id}/involvement",
        params={"status": "inactive"},
        headers={"Authorization": f"Bearer {session['access_token']}"},
    )
    assert response.status_code == 403, response.text


def test_email_lookup_is_limited_to_the_current_care_record():
    client = TestClient(app)
    first = create_session(client, "First person")
    second = create_session(client, "Second person")
    other_profile_id = second["caregiver"]["profile_id"]
    db = TestSessionLocal()
    try:
        other_profile = db.query(CaregiverProfile).filter_by(id=other_profile_id).one()
        other_profile.email = "second@example.invalid"
        db.commit()
    finally:
        db.close()

    response = client.get(
        "/api/v1/caregivers/by-email",
        params={"email": "second@example.invalid", "person_id": first["person"]["id"]},
        headers={"Authorization": f"Bearer {first['access_token']}"},
    )
    assert response.status_code == 404, response.text
    assert response.json().get("id") != other_profile_id


def test_caregiver_observation_write_uses_server_bound_profile_identity():
    client = TestClient(app)
    session = create_session(client)
    person_id = session["person"]["id"]
    headers = {"Authorization": f"Bearer {session['access_token']}"}

    created = client.post(
        "/api/v1/caregiver-observations",
        headers=headers,
        json={
            "person_id": person_id,
            "caregiver_id": session["caregiver"]["id"],
            "observation_content": "Caregiver-reported breakfast observation",
            "information_visibility": "private",
            "is_promoted_to_shared": False,
        },
    )
    assert created.status_code == 200, created.text
    assert created.json()["caregiver_id"] == session["caregiver"]["profile_id"]

    other_profile_id, _ = create_second_profile(person_id)
    spoofed = client.post(
        "/api/v1/caregiver-observations",
        headers=headers,
        json={
            "person_id": person_id,
            "caregiver_id": other_profile_id,
            "observation_content": "Attempted actor spoof",
            "information_visibility": "private",
            "is_promoted_to_shared": False,
        },
    )
    assert spoofed.status_code == 403, spoofed.text
