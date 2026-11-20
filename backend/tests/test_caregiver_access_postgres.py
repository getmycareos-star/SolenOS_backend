"""PostgreSQL acceptance for invited caregiver identity, isolation and revocation.

Unlike the SQLite transition tests, this exercises the real migrated PostgreSQL
schema and the application middleware with two distinct bearer identities.
"""
from datetime import datetime, timedelta, timezone
import os
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="requires migrated PostgreSQL DATABASE_URL")

from app.main import app
from app.models.anonymous_session import AnonymousSession
from app.models.care import Caregiver, Person
from app.models.caregiver_access import CaregiverAccessGrant, CaregiverInvitation
from app.models.caregiver_account import CaregiverAccount
from app.models.privacy_delivery import AuditLogEvent
from app.models.caregiver_intelligence import CaregiverProfile

engine = create_engine(DATABASE_URL, pool_pre_ping=True) if DATABASE_URL else None
Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False) if engine else None


def test_postgres_invited_caregiver_is_isolated_and_revocation_invalidates_existing_session():
    suffix = uuid.uuid4().hex[:10]
    invite_email = f"invited-{suffix}@example.test"
    primary_email = f"primary-{suffix}@example.test"
    client = TestClient(app)
    person_id = None
    primary_caregiver_id = None
    invited_caregiver_id = None
    invitation_id = None
    foreign_person_id = None

    try:
        primary = client.post(
            "/api/v1/sessions/anonymous",
            json={"name": f"Shared-care person {suffix}", "caregiver_name": "Primary caregiver"},
        )
        assert primary.status_code == 201, primary.text
        primary_body = primary.json()
        person_id = primary_body["person"]["id"]
        primary_caregiver_id = primary_body["caregiver"]["id"]
        primary_headers = {"Authorization": f"Bearer {primary_body['access_token']}"}

        account = client.post(
            "/api/v1/accounts",
            headers=primary_headers,
            json={"email": primary_email, "password": "primary-postgres-password-456"},
        )
        assert account.status_code == 201, account.text

        created = client.post(
            "/api/v1/caregiver-access/invitations",
            headers=primary_headers,
            json={"email": invite_email, "expires_in_days": 7},
        )
        assert created.status_code == 201, created.text
        invite = created.json()
        invitation_id = invite["invitation_id"]
        assert invite["status"] == "pending"
        assert invite["delivery_status"] == "not_sent"

        wrong_recipient = client.post(
            "/api/v1/caregiver-access/invitations/accept",
            json={
                "token": invite["invitation_token"],
                "email": f"attacker-{suffix}@example.test",
                "password": "attacker-postgres-password-456",
                "name": "Unauthorised recipient",
            },
        )
        assert wrong_recipient.status_code == 403

        accepted = client.post(
            "/api/v1/caregiver-access/invitations/accept",
            json={
                "token": invite["invitation_token"],
                "email": invite_email.upper(),
                "password": "invited-postgres-password-456",
                "name": "Invited caregiver",
            },
        )
        assert accepted.status_code == 201, accepted.text
        invited = accepted.json()
        invited_caregiver_id = invited["caregiver_id"]
        original_invited_session_id = invited["session_id"]
        invited_headers = {"Authorization": f"Bearer {invited['access_token']}"}

        expired_email = f"expired-{suffix}@example.test"
        expired_invite = client.post(
            "/api/v1/caregiver-access/invitations",
            headers=primary_headers,
            json={"email": expired_email, "expires_in_days": 7},
        )
        assert expired_invite.status_code == 201, expired_invite.text
        with Session() as db:
            stored_pending = db.query(CaregiverInvitation).filter_by(
                id=expired_invite.json()["invitation_id"]
            ).one()
            stored_pending.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
            db.commit()
        expired_acceptance = client.post(
            "/api/v1/caregiver-access/invitations/accept",
            json={
                "token": expired_invite.json()["invitation_token"],
                "email": expired_email,
                "password": "expired-invite-password-456",
                "name": "Expired invite",
            },
        )
        assert expired_acceptance.status_code == 410, expired_acceptance.text
        with Session() as db:
            expired_row = db.query(CaregiverInvitation).filter_by(
                id=expired_invite.json()["invitation_id"]
            ).one()
            assert expired_row.status == "expired"
        assert invited["person_id"] == person_id
        assert invited_caregiver_id != primary_caregiver_id

        suspended = client.patch(
            f"/api/v1/caregiver-access/members/{invited_caregiver_id}/status",
            headers=primary_headers,
            json={"status": "suspended"},
        )
        assert suspended.status_code == 200, suspended.text
        assert suspended.json()["status"] == "suspended"
        assert suspended.json()["sessions_revoked"] is True
        suspended_assignable = client.get(
            "/api/v1/caregiver-access/assignable-members", headers=primary_headers
        )
        assert suspended_assignable.status_code == 200, suspended_assignable.text
        assert [row["caregiver_id"] for row in suspended_assignable.json()] == [primary_caregiver_id]
        suspended_session = client.get(
            f"/api/v1/evidence/{person_id}", headers=invited_headers
        )
        assert suspended_session.status_code == 401, suspended_session.text
        blocked_login = client.post(
            "/api/v1/accounts/login",
            json={"email": invite_email, "password": "invited-postgres-password-456"},
        )
        assert blocked_login.status_code == 403, blocked_login.text

        restored = client.patch(
            f"/api/v1/caregiver-access/members/{invited_caregiver_id}/status",
            headers=primary_headers,
            json={"status": "active"},
        )
        assert restored.status_code == 200, restored.text
        assert restored.json()["reauthentication_required"] is True
        relogin = client.post(
            "/api/v1/accounts/login",
            json={"email": invite_email, "password": "invited-postgres-password-456"},
        )
        assert relogin.status_code == 200, relogin.text
        invited = relogin.json()
        restored_invited_session_id = invited["session_id"]
        invited_headers = {"Authorization": f"Bearer {invited['access_token']}"}

        primary_assignable = client.get(
            "/api/v1/caregiver-access/assignable-members", headers=primary_headers
        )
        invited_assignable = client.get(
            "/api/v1/caregiver-access/assignable-members", headers=invited_headers
        )
        assert primary_assignable.status_code == 200, primary_assignable.text
        assert invited_assignable.status_code == 200, invited_assignable.text
        assert {row["caregiver_id"] for row in primary_assignable.json()} == {
            primary_caregiver_id, invited_caregiver_id
        }

        # Both identities can access this one authorised record before revocation.
        primary_record = client.get(
            f"/api/v1/evidence/{person_id}", headers=primary_headers
        )
        invited_record = client.get(
            f"/api/v1/evidence/{person_id}", headers=invited_headers
        )
        assert primary_record.status_code == 200, primary_record.text
        assert invited_record.status_code == 200, invited_record.text

        # A distinct identity/person cannot use a known person ID as a scope override.
        foreign = client.post(
            "/api/v1/sessions/anonymous",
            json={"name": f"Foreign person {suffix}", "caregiver_name": "Foreign caregiver"},
        )
        assert foreign.status_code == 201, foreign.text
        foreign_person_id = foreign.json()["person"]["id"]
        foreign_headers = {"Authorization": f"Bearer {foreign.json()['access_token']}"}
        cross_person = client.get(
            f"/api/v1/evidence/{person_id}", headers=foreign_headers
        )
        assert cross_person.status_code == 403, cross_person.text

        revoked = client.delete(
            f"/api/v1/caregiver-access/members/{invited_caregiver_id}",
            headers=primary_headers,
        )
        assert revoked.status_code == 200, revoked.text
        assert revoked.json()["sessions_revoked"] is True

        stale_session = client.get(
            f"/api/v1/evidence/{person_id}", headers=invited_headers
        )
        assert stale_session.status_code == 401, stale_session.text
        primary_still_authorised = client.get(
            f"/api/v1/evidence/{person_id}", headers=primary_headers
        )
        assert primary_still_authorised.status_code == 200, primary_still_authorised.text
        active_assignable = client.get(
            "/api/v1/caregiver-access/assignable-members", headers=primary_headers
        )
        assert active_assignable.status_code == 200, active_assignable.text
        assert [row["caregiver_id"] for row in active_assignable.json()] == [primary_caregiver_id]

        # Verify the transition in a fresh PostgreSQL session, not the request ORM cache.
        with Session() as db:
            stored_invitation = db.query(CaregiverInvitation).filter_by(id=invitation_id).one()
            stored_grant = db.query(CaregiverAccessGrant).filter_by(
                person_id=person_id, caregiver_id=invited_caregiver_id
            ).one()
            stored_session = db.query(AnonymousSession).filter_by(id=original_invited_session_id).one()
            restored_session = db.query(AnonymousSession).filter_by(id=restored_invited_session_id).one()
            assert stored_invitation.status == "accepted"
            assert stored_invitation.accepted_caregiver_id == invited_caregiver_id
            assert stored_grant.status == "revoked"
            assert stored_grant.revoked_at is not None
            access_events = db.query(AuditLogEvent).filter_by(
                person_id=person_id,
                target_type="caregiver_access_grant",
                target_id=stored_grant.id,
            ).order_by(AuditLogEvent.occurred_at.asc(), AuditLogEvent.id.asc()).all()
            assert [event.action for event in access_events] == [
                "caregiver.access.suspended",
                "caregiver.access.restored",
                "caregiver.access.revoked",
            ]
            assert all(event.actor_id == primary_caregiver_id for event in access_events)
            assert stored_session.revoked_at is not None
            assert restored_session.revoked_at is not None
            assert stored_session.expires_at > datetime.now(timezone.utc)
    finally:
        if Session and (person_id or foreign_person_id):
            with Session() as db:
                if invitation_id:
                    db.query(CaregiverInvitation).filter_by(id=invitation_id).delete(synchronize_session=False)
                db.query(CaregiverAccessGrant).filter_by(person_id=person_id).delete(synchronize_session=False)
                db.query(AnonymousSession).filter_by(person_id=person_id).delete(synchronize_session=False)
                db.query(CaregiverAccount).filter_by(person_id=person_id).delete(synchronize_session=False)
                db.query(CaregiverProfile).filter_by(person_id=person_id).delete(synchronize_session=False)
                db.query(Caregiver).filter_by(person_id=person_id).delete(synchronize_session=False)
                if foreign_person_id:
                    db.query(AnonymousSession).filter_by(person_id=foreign_person_id).delete(synchronize_session=False)
                    db.query(Caregiver).filter_by(person_id=foreign_person_id).delete(synchronize_session=False)
                    db.query(Person).filter_by(id=foreign_person_id).delete(synchronize_session=False)
                if person_id:
                    db.query(Person).filter_by(id=person_id).delete(synchronize_session=False)
                db.commit()
        if engine:
            engine.dispose()
