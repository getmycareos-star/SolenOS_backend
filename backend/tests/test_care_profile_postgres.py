"""PostgreSQL acceptance for care-recipient profile persistence.

This exercises the production route against the migrated PostgreSQL schema,
then signs in again to prove the profile survives a new authorized session.
"""
import os
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="requires migrated PostgreSQL DATABASE_URL")

from app.main import app
from app.models.care import Person
from app.models.caregiver_intelligence import CaregiverContext, CaregiverProfile

engine = create_engine(DATABASE_URL, pool_pre_ping=True) if DATABASE_URL else None
Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False) if engine else None


def test_care_profile_write_read_new_session_history_and_record_isolation():
    suffix = uuid.uuid4().hex[:12]
    email = f"care-profile-{suffix}@example.test"
    password = f"care-profile-password-{suffix}"
    client = TestClient(app)

    created = client.post(
        "/api/v1/sessions/anonymous",
        json={"name": f"Initial recipient {suffix}", "caregiver_name": f"Primary caregiver {suffix}"},
    )
    assert created.status_code == 201, created.text
    initial = created.json()
    first_person_id = initial["person"]["id"]
    first_caregiver_id = initial["caregiver"]["id"]
    first_headers = {"Authorization": f"Bearer {initial['access_token']}"}

    account = client.post(
        "/api/v1/accounts",
        headers=first_headers,
        json={"email": email, "password": password},
    )
    assert account.status_code == 201, account.text

    saved = client.put(
        "/api/v1/care-profile",
        headers=first_headers,
        json={
            "name": f"Updated recipient {suffix}",
            "relationship": "Daughter",
            "condition": "Dementia stage reported by caregiver",
        },
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["name"] == f"Updated recipient {suffix}"
    assert saved.json()["relationship"] == "Daughter"
    assert saved.json()["condition"] == "Dementia stage reported by caregiver"

    # A fresh login issues a new bearer session. The data must be retrieved
    # from PostgreSQL, not restored from browser state.
    login = client.post(
        "/api/v1/accounts/login",
        json={"email": email, "password": password},
    )
    assert login.status_code == 200, login.text
    restored = login.json()
    assert restored["person_id"] == first_person_id
    assert restored["caregiver_id"] == first_caregiver_id
    restored_headers = {"Authorization": f"Bearer {restored['access_token']}"}
    readback = client.get("/api/v1/care-profile", headers=restored_headers)
    assert readback.status_code == 200, readback.text
    assert readback.json() == saved.json()

    # A second independent care record cannot read or mutate the first record.
    second = client.post(
        "/api/v1/sessions/anonymous",
        json={"name": f"Separate recipient {suffix}", "caregiver_name": f"Second caregiver {suffix}"},
    )
    assert second.status_code == 201, second.text
    second_headers = {"Authorization": f"Bearer {second.json()['access_token']}"}
    second_read = client.get("/api/v1/care-profile", headers=second_headers)
    assert second_read.status_code == 200, second_read.text
    assert second_read.json()["name"] == f"Separate recipient {suffix}"
    second_write = client.put(
        "/api/v1/care-profile",
        headers=second_headers,
        json={"name": f"Separate recipient updated {suffix}", "relationship": "Son", "condition": None},
    )
    assert second_write.status_code == 200, second_write.text

    # Clear the condition by writing a new version; prior attribution remains.
    cleared = client.put(
        "/api/v1/care-profile",
        headers=restored_headers,
        json={"name": f"Updated recipient {suffix}", "relationship": "Daughter", "condition": None},
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["condition"] is None

    with Session() as db:
        first_person = db.query(Person).filter_by(id=first_person_id).one()
        first_profile = db.query(CaregiverProfile).filter_by(session_caregiver_id=first_caregiver_id).one()
        versions = (
            db.query(CaregiverContext)
            .filter_by(person_id=first_person_id, caregiver_id=first_profile.id)
            .all()
        )
        profile_versions = [
            row for row in versions
            if isinstance(row.context_metadata, dict)
            and row.context_metadata.get("record_type") == "care_recipient_profile"
        ]
        assert first_person.name == f"Updated recipient {suffix}"
        assert first_profile.relationship == "Daughter"
        assert len(profile_versions) == 2
        assert all(row.effective_start is not None for row in profile_versions)
        assert all(row.context_metadata.get("epistemic_status") == "caregiver_reported" for row in profile_versions)
        assert any(row.context_metadata.get("condition") == "Dementia stage reported by caregiver" for row in profile_versions)
        assert any(row.context_metadata.get("condition") is None for row in profile_versions)
        assert any(row.effective_end is not None for row in profile_versions)

        second_person = db.query(Person).filter_by(id=second.json()["person"]["id"]).one()
        assert second_person.name == f"Separate recipient updated {suffix}"
        assert second_person.id != first_person.id
