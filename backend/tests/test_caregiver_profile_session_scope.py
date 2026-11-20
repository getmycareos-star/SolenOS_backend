import pytest
from datetime import datetime, timezone
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.main as main_module
from app.core.database import Base, get_db, SessionLocal as DefaultSessionLocal
from app.main import app
from app.models.care import Caregiver, Person
from app.models.caregiver_intelligence import CaregiverProfile, CaregiverContext, CaregiverObservation, CaregiverHandoff, CaregiverRole, CaregiverResponsibility
from app.core.caregiver_enums import InformationVisibility, CaregiverRoleType

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


def test_care_recipient_profile_persists_and_isolated_by_bearer_session():
    client = TestClient(app)
    first = create_session(client, "Initial recipient")
    second = create_session(client, "Separate recipient")
    first_headers = {"Authorization": f"Bearer {first['access_token']}"}
    second_headers = {"Authorization": f"Bearer {second['access_token']}"}

    initial = client.get("/api/v1/care-profile", headers=first_headers)
    assert initial.status_code == 200, initial.text
    assert initial.json()["name"] == "Initial recipient"

    saved = client.put(
        "/api/v1/care-profile",
        headers=first_headers,
        json={
            "name": "Updated recipient",
            "relationship": "Daughter",
            "condition": "Dementia, caregiver-reported profile context",
        },
    )
    assert saved.status_code == 200, saved.text
    assert saved.json() == {
        "name": "Updated recipient",
        "relationship": "Daughter",
        "condition": "Dementia, caregiver-reported profile context",
    }

    # The second bearer cannot read or overwrite the first person's record.
    second_read = client.get(
        f"/api/v1/care-profile?person_id={first['person']['id']}",
        headers=second_headers,
    )
    assert second_read.status_code == 200, second_read.text
    assert second_read.json()["name"] == "Separate recipient"
    second_write = client.put(
        f"/api/v1/care-profile?person_id={first['person']['id']}",
        headers=second_headers,
        json={"name": "Attempted cross-record overwrite", "relationship": "Other", "condition": None},
    )
    assert second_write.status_code == 200, second_write.text
    assert second_write.json()["name"] == "Attempted cross-record overwrite"

    db = TestSessionLocal()
    try:
        first_person = db.query(Person).filter_by(id=first["person"]["id"]).one()
        first_profile = db.query(CaregiverProfile).filter_by(id=first["caregiver"]["profile_id"]).one()
        first_contexts = (
            db.query(CaregiverContext)
            .filter_by(person_id=first["person"]["id"], caregiver_id=first_profile.id)
            .all()
        )
        assert first_person.name == "Updated recipient"
        assert first_profile.relationship == "Daughter"
        profile_versions = [
            row for row in first_contexts
            if isinstance(row.context_metadata, dict)
            and row.context_metadata.get("record_type") == "care_recipient_profile"
        ]
        assert len(profile_versions) == 1
        assert profile_versions[0].context_metadata["condition"] == "Dementia, caregiver-reported profile context"
        assert profile_versions[0].context_metadata["epistemic_status"] == "caregiver_reported"
        assert profile_versions[0].context_metadata["reported_by_session_caregiver_id"] == first["caregiver"]["id"]

        second_person = db.query(Person).filter_by(id=second["person"]["id"]).one()
        assert second_person.name == "Attempted cross-record overwrite"
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
        params={"person_id": person_id, "caregiver_id": other_profile_id},
        headers=headers,
    )
    assert filtered.status_code == 200, filtered.text
    assert filtered.json() == []

    direct = client.get(
        f"/api/v1/caregiver-observations/{person_id}/caregiver/{other_profile_id}",
        params={"person_id": person_id},
        headers=headers,
    )
    assert direct.status_code == 403, direct.text

    promote = client.post(
        f"/api/v1/caregiver-observations/{observation_id}/promote",
        params={"person_id": person_id, "caregiver_id": own_profile_id},
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
        params={"person_id": person_id, "acceptance_evidence": "accepted"},
    )
    assert response.status_code == 404, response.text

    db = TestSessionLocal()
    try:
        persisted = db.query(CaregiverHandoff).filter_by(id=handoff_id).one()
        assert persisted.is_completed is False
    finally:
        db.close()


def test_caregiver_role_lifecycle_is_scoped_and_persisted():
    client = TestClient(app)
    session = create_session(client)
    person_id = session["person"]["id"]
    own_profile_id = session["caregiver"]["profile_id"]
    headers = {"Authorization": f"Bearer {session['access_token']}"}

    created = client.post(
        "/api/v1/caregiver-roles",
        headers=headers,
        json={
            "person_id": person_id,
            "caregiver_id": own_profile_id,
            "role_type": "appointment_coordinator",
            "scope": "Coordinate appointment dates and transport arrangements.",
            "effective_start": "2026-11-19T00:00:00+00:00",
            "is_current": True,
            "assignment_source": "caregiver_self_reported",
        },
    )
    assert created.status_code == 200, created.text
    role_id = created.json()["id"]
    listed = client.get(f"/api/v1/caregiver-roles/{person_id}", headers=headers)
    assert listed.status_code == 200, listed.text
    assert any(item["id"] == role_id for item in listed.json())

    other_profile_id, _ = create_second_profile(person_id)
    db = TestSessionLocal()
    try:
        other_role = CaregiverRole(
            person_id=person_id,
            caregiver_id=other_profile_id,
            role_type=CaregiverRoleType.MEDICATION_MANAGER,
            effective_start=datetime.now(timezone.utc),
            is_current=True,
            assignment_source="test_fixture",
        )
        db.add(other_role)
        db.commit()
        other_role_id = other_role.id
    finally:
        db.close()

    denied = client.patch(f"/api/v1/caregiver-roles/{other_role_id}/end", headers=headers)
    assert denied.status_code == 404, denied.text

    ended = client.patch(f"/api/v1/caregiver-roles/{role_id}/end", headers=headers)
    assert ended.status_code == 200, ended.text
    assert ended.json()["is_current"] is False
    assert ended.json()["effective_end"] is not None
    remaining = client.get(f"/api/v1/caregiver-roles/{person_id}", headers=headers)
    assert remaining.status_code == 200, remaining.text
    assert all(item["id"] != role_id for item in remaining.json())


def test_caregiver_responsibility_lifecycle_is_scoped_and_persisted():
    client = TestClient(app)
    session = create_session(client)
    person_id = session["person"]["id"]
    own_profile_id = session["caregiver"]["profile_id"]
    headers = {"Authorization": f"Bearer {session['access_token']}"}

    created = client.post(
        "/api/v1/caregiver-responsibilities",
        headers=headers,
        json={
            "person_id": person_id,
            "caregiver_id": own_profile_id,
            "responsibility_type": "appointment_follow_up",
            "scope": "Confirm appointment time and arrange transport.",
            "effective_start": "2026-11-19T00:00:00+00:00",
            "status": "active",
            "acceptance_status": "explicit",
            "assignment_source": "caregiver_self_reported",
        },
    )
    assert created.status_code == 200, created.text
    responsibility_id = created.json()["id"]
    active = client.get(f"/api/v1/caregiver-responsibilities/{person_id}", headers=headers)
    assert active.status_code == 200, active.text
    assert any(item["id"] == responsibility_id for item in active.json())

    other_profile_id, _ = create_second_profile(person_id)
    db = TestSessionLocal()
    try:
        other_responsibility = CaregiverResponsibility(
            person_id=person_id,
            caregiver_id=other_profile_id,
            responsibility_type="medication_support",
            effective_start=datetime.now(timezone.utc),
        )
        db.add(other_responsibility)
        db.commit()
        other_responsibility_id = other_responsibility.id
    finally:
        db.close()

    denied = client.patch(
        f"/api/v1/caregiver-responsibilities/{other_responsibility_id}/end",
        headers=headers,
    )
    assert denied.status_code == 404, denied.text

    ended = client.patch(
        f"/api/v1/caregiver-responsibilities/{responsibility_id}/end",
        headers=headers,
    )
    assert ended.status_code == 200, ended.text
    assert ended.json()["status"] == "historical"
    assert ended.json()["effective_end"] is not None
    remaining = client.get(f"/api/v1/caregiver-responsibilities/{person_id}", headers=headers)
    assert remaining.status_code == 200, remaining.text
    assert all(item["id"] != responsibility_id for item in remaining.json())


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
    # Cross-record scope rejection may be 403 at the authorization
    # boundary or 404 after a scoped lookup. Neither response may disclose
    # the other caregiver profile.
    assert response.status_code in {403, 404}, response.text
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

def test_current_caregiver_profile_is_resolved_from_authenticated_session():
    client = TestClient(app)
    session = create_session(client)
    response = client.get(
        "/api/v1/caregiver-profiles/current",
        headers={"Authorization": f"Bearer {session['access_token']}"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["id"] == session["caregiver"]["profile_id"]
    assert response.json()["person_id"] == session["person"]["id"]
