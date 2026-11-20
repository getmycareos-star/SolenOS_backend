"""PostgreSQL acceptance for care-recipient profile persistence.

This exercises the production route against the migrated PostgreSQL schema,
then signs in again to prove the profile survives a new authorized session.
"""
import importlib.util
import os
from pathlib import Path
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="requires migrated PostgreSQL DATABASE_URL")

from app.main import app
from app.models.care import Person
from app.models.caregiver_intelligence import CaregiverContext, CaregiverProfile
from app.models.privacy_delivery import AuditLogEvent

engine = create_engine(DATABASE_URL, pool_pre_ping=True) if DATABASE_URL else None
Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False) if engine else None


def test_care_profile_write_read_new_session_history_and_record_isolation():
    schema_columns = {
        column["name"]: column
        for column in inspect(engine).get_columns("caregiver_contexts")
    }
    required_context_columns = {
        "proximity_category",
        "observation_window_start",
        "observation_window_end",
        "availability_notes",
        "context_metadata",
        "effective_start",
        "effective_end",
        "reported_by_caregiver_id",
    }
    assert required_context_columns <= set(schema_columns)
    assert schema_columns["effective_start"]["nullable"] is True
    assert schema_columns["person_id"]["nullable"] is False
    assert schema_columns["created_at"]["nullable"] is False
    context_indexes = {
        tuple(index.get("column_names") or ())
        for index in inspect(engine).get_indexes("caregiver_contexts")
    }
    assert ("person_id",) in context_indexes
    assert ("caregiver_id",) in context_indexes
    context_foreign_keys = {
        (tuple(item.get("constrained_columns") or ()), item.get("referred_table"))
        for item in inspect(engine).get_foreign_keys("caregiver_contexts")
    }
    assert (("person_id",), "persons") in context_foreign_keys
    assert (("caregiver_id",), "caregiver_profiles") in context_foreign_keys
    assert (("reported_by_caregiver_id",), "caregivers") in context_foreign_keys

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

    context_write = client.post(
        "/api/caregiver-contexts",
        headers=first_headers,
        json={
            "caregiver_id": first_caregiver_id,
            "person_id": first_person_id,
            "effective_start": "2026-11-20T12:00:00+00:00",
            "context_metadata": {"record_type": "attribution_test"},
        },
    )
    assert context_write.status_code == 200, context_write.text
    assert context_write.json()["reported_by_caregiver_id"] == first_caregiver_id

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

    # An invited caregiver on the same care record sees shared recipient
    # context but retains an independent caregiver-to-recipient relationship.
    invited_email = f"care-profile-invited-{suffix}@example.test"
    invitation = client.post(
        "/api/v1/caregiver-access/invitations",
        headers=restored_headers,
        json={"email": invited_email, "expires_in_days": 7},
    )
    assert invitation.status_code == 201, invitation.text
    accepted = client.post(
        "/api/v1/caregiver-access/invitations/accept",
        json={
            "token": invitation.json()["invitation_token"],
            "email": invited_email,
            "password": f"invited-care-profile-password-{suffix}",
            "name": f"Invited caregiver {suffix}",
        },
    )
    assert accepted.status_code == 201, accepted.text
    invited = accepted.json()
    invited_headers = {"Authorization": f"Bearer {invited['access_token']}"}
    invited_read = client.get("/api/v1/care-profile", headers=invited_headers)
    assert invited_read.status_code == 200, invited_read.text
    assert invited_read.json()["name"] == f"Updated recipient {suffix}"
    assert invited_read.json()["condition"] == "Dementia stage reported by caregiver"
    invited_saved = client.put(
        "/api/v1/care-profile",
        headers=invited_headers,
        json={
            "name": f"Updated recipient {suffix}",
            "relationship": "Son",
            "condition": "Dementia stage reported by caregiver",
        },
    )
    assert invited_saved.status_code == 200, invited_saved.text
    assert invited_saved.json()["relationship"] == "Son"
    primary_after_invited_update = client.get("/api/v1/care-profile", headers=restored_headers)
    assert primary_after_invited_update.status_code == 200, primary_after_invited_update.text
    assert primary_after_invited_update.json()["relationship"] == "Daughter"
    assert primary_after_invited_update.json()["condition"] == "Dementia stage reported by caregiver"

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
        versions = db.query(CaregiverContext).filter_by(person_id=first_person_id).all()
        profile_versions = [
            row for row in versions
            if row.caregiver_id is None
            and isinstance(row.context_metadata, dict)
            and row.context_metadata.get("record_type") == "care_recipient_profile"
        ]
        relationship_versions = [
            row for row in versions
            if row.caregiver_id == first_profile.id
            and isinstance(row.context_metadata, dict)
            and row.context_metadata.get("record_type") == "caregiver_relationship"
        ]
        assert first_person.name == f"Updated recipient {suffix}"
        assert first_profile.relationship == "Daughter"
        assert len(profile_versions) == 2
        assert all(row.reported_by_caregiver_id == first_caregiver_id for row in profile_versions)
        assert all(row.effective_start is not None for row in profile_versions)
        assert all(row.context_metadata.get("epistemic_status") == "caregiver_reported" for row in profile_versions)
        assert any(row.context_metadata.get("condition") == "Dementia stage reported by caregiver" for row in profile_versions)
        assert any(row.context_metadata.get("condition") is None for row in profile_versions)
        assert any(row.effective_end is not None for row in profile_versions)
        assert any(row.context_metadata.get("recipient_name") == f"Updated recipient {suffix}" for row in profile_versions)
        assert len(relationship_versions) == 1
        assert relationship_versions[0].reported_by_caregiver_id == first_caregiver_id
        assert relationship_versions[0].context_metadata.get("caregiver_relationship") == "Daughter"
        audit_events = db.query(AuditLogEvent).filter_by(
            person_id=first_person_id,
            caregiver_id=first_caregiver_id,
            action="care_recipient_profile_updated",
        ).all()
        assert len(audit_events) == 2
        assert all(event.outcome == "success" for event in audit_events)

        second_person = db.query(Person).filter_by(id=second.json()["person"]["id"]).one()
        assert second_person.name == f"Separate recipient updated {suffix}"
        assert second_person.id != first_person.id


def _load_notification_delivery_migration():
    migration_path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "20261120_07_notification_delivery_reconcile.py"
    )
    spec = importlib.util.spec_from_file_location(
        "notification_delivery_reconcile_migration",
        migration_path,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_legacy_notification_attempt_backfill_preserves_unknown_and_orders_retries():
    migration = _load_notification_delivery_migration()
    with engine.begin() as connection:
        connection.execute(text("""
            CREATE TEMP TABLE notification_delivery_attempts (
                id VARCHAR(36) PRIMARY KEY,
                notification_id VARCHAR(36) NOT NULL,
                attempt_number INTEGER NULL,
                channel TEXT NOT NULL,
                attempted_at TIMESTAMPTZ NULL,
                result TEXT NULL,
                metadata JSON NULL,
                created_at TIMESTAMPTZ NOT NULL,
                updated_at TIMESTAMPTZ NULL
            ) ON COMMIT DROP
        """))
        connection.execute(text("""
            INSERT INTO notification_delivery_attempts
                (id, notification_id, attempt_number, channel, attempted_at,
                 result, metadata, created_at, updated_at)
            VALUES
                ('attempt-known', 'notification-a', 2, 'in_app',
                 '2026-11-01T09:00:00Z', 'accepted', '{"provider":"known"}',
                 '2026-11-01T09:00:00Z', '2026-11-01T09:00:00Z'),
                ('attempt-a-missing', 'notification-a', NULL, 'in_app',
                 NULL, NULL, NULL, '2026-11-02T09:00:00Z', NULL),
                ('attempt-b-missing', 'notification-b', NULL, 'in_app',
                 NULL, NULL, NULL, '2026-11-03T09:00:00Z', NULL)
        """))

        migration._backfill_nullable_attempt_values(connection)
        rows = {
            row["id"]: row
            for row in connection.execute(text("""
                SELECT id, notification_id, attempt_number, attempted_at,
                       result, metadata, created_at, updated_at
                FROM notification_delivery_attempts
            """)).mappings()
        }

        assert rows["attempt-known"]["attempt_number"] == 2
        assert rows["attempt-known"]["result"] == "accepted"
        assert rows["attempt-a-missing"]["attempt_number"] == 3
        assert rows["attempt-b-missing"]["attempt_number"] == 1
        for key in ("attempt-a-missing", "attempt-b-missing"):
            assert rows[key]["attempted_at"] == rows[key]["created_at"]
            assert rows[key]["updated_at"] == rows[key]["created_at"]
            assert rows[key]["result"] == "unknown_legacy"
            assert rows[key]["metadata"] == {}


def test_legacy_notification_attempt_backfill_refuses_to_invent_missing_created_at():
    migration = _load_notification_delivery_migration()
    with engine.begin() as connection:
        connection.execute(text("""
            CREATE TEMP TABLE notification_delivery_attempts (
                id VARCHAR(36) PRIMARY KEY,
                notification_id VARCHAR(36) NOT NULL,
                attempt_number INTEGER NULL,
                channel TEXT NOT NULL,
                attempted_at TIMESTAMPTZ NULL,
                result TEXT NULL,
                metadata JSON NULL,
                created_at TIMESTAMPTZ NULL,
                updated_at TIMESTAMPTZ NULL
            ) ON COMMIT DROP
        """))
        connection.execute(text("""
            INSERT INTO notification_delivery_attempts
                (id, notification_id, attempt_number, channel, created_at)
            VALUES ('attempt-no-timestamp', 'notification-a', NULL, 'in_app', NULL)
        """))
        with pytest.raises(RuntimeError, match="no created_at timestamp"):
            migration._backfill_nullable_attempt_values(connection)
