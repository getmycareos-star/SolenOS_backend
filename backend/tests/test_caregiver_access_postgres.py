"""PostgreSQL acceptance for invited caregiver identity, isolation and revocation.

Unlike the SQLite transition tests, this exercises the real migrated PostgreSQL
schema and the application middleware with two distinct bearer identities.
"""
from datetime import datetime, timedelta, timezone
import os
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="requires migrated PostgreSQL DATABASE_URL")

from app.main import app
from app.models.anonymous_session import AnonymousSession
from app.models.care import Caregiver, Evidence, Person
from app.models.caregiver_access import CaregiverAccessGrant, CaregiverInvitation, CaregiverResponsibilityTransfer
from app.models.caregiver_account import CaregiverAccount
from app.models.privacy_delivery import AuditLogEvent
from app.models.care_continuity import CareDeltaRun, CareStateRevision
from app.models.caregiver_intelligence import CaregiverProfile, CaregiverResponsibility, CaregiverHandoff
from app.models.open_loop import OpenLoopRecord, OpenLoopEvent, OpenLoopOccurrence

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

        recovery = client.post("/api/v1/accounts/recovery-code", headers=invited_headers)
        assert recovery.status_code == 200, recovery.text
        recovery_code = recovery.json()["recovery_code"]
        recovered = client.post(
            "/api/v1/accounts/recover",
            json={
                "email": invite_email,
                "recovery_code": recovery_code,
                "new_password": "invited-after-recovery-password-456",
            },
        )
        assert recovered.status_code == 200, recovered.text
        stale_before_recovery = client.get(
            f"/api/v1/evidence/{person_id}", headers=invited_headers
        )
        assert stale_before_recovery.status_code == 401, stale_before_recovery.text
        recovered_login = client.post(
            "/api/v1/accounts/login",
            json={
                "email": invite_email,
                "password": "invited-after-recovery-password-456",
            },
        )
        assert recovered_login.status_code == 200, recovered_login.text
        invited = recovered_login.json()
        recovery_session_id = invited["session_id"]
        invited_headers = {"Authorization": f"Bearer {invited['access_token']}"}

        restricted = client.patch(
            f"/api/v1/caregiver-access/members/{invited_caregiver_id}/access-scope",
            headers=primary_headers,
            json={"access_scope": "documents_only"},
        )
        assert restricted.status_code == 200, restricted.text
        assert client.get(
            f"/api/v1/care-delta/runs/{person_id}", headers=invited_headers
        ).status_code == 403
        assert client.get(
            "/api/v1/handoffs/inbox", headers=invited_headers
        ).status_code == 403
        assert client.get(
            "/api/v1/caregiver-access/assignable-members", headers=invited_headers
        ).status_code == 403
        full_access = client.patch(
            f"/api/v1/caregiver-access/members/{invited_caregiver_id}/access-scope",
            headers=primary_headers,
            json={"access_scope": "full_record"},
        )
        assert full_access.status_code == 200, full_access.text

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
            recovered_session = db.query(AnonymousSession).filter_by(id=recovery_session_id).one()
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
                "caregiver.access_scope.updated",
                "caregiver.access_scope.updated",
                "caregiver.access.revoked",
            ]
            assert all(event.actor_id == primary_caregiver_id for event in access_events)
            assert stored_session.revoked_at is not None
            assert restored_session.revoked_at is not None
            assert recovered_session.revoked_at is not None
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



def test_postgres_two_caregivers_concurrent_evidence_writes_preserve_attribution_and_shared_reads():
    suffix = uuid.uuid4().hex[:10]
    invite_email = f"concurrent-{suffix}@example.test"
    person_id = None
    primary_caregiver_id = None
    invited_caregiver_id = None
    evidence_ids = []

    try:
        with TestClient(app) as client:
            primary = client.post(
                "/api/v1/sessions/anonymous",
                json={"caregiver_name": f"Primary {suffix}"},
            )
            assert primary.status_code == 201, primary.text
            primary_body = primary.json()
            person_id = primary_body["person"]["id"]
            primary_caregiver_id = primary_body["caregiver"]["id"]
            primary_headers = {"Authorization": f"Bearer {primary_body['access_token']}"}

            account = client.post(
                "/api/v1/accounts",
                headers=primary_headers,
                json={"email": invite_email, "password": "concurrent-primary-password-456"},
            )
            assert account.status_code == 201, account.text
            invitation = client.post(
                "/api/v1/caregiver-access/invitations",
                headers=primary_headers,
                json={"email": invite_email, "expires_in_days": 7},
            )
            assert invitation.status_code == 201, invitation.text
            accepted = client.post(
                "/api/v1/caregiver-access/invitations/accept",
                json={
                    "token": invitation.json()["invitation_token"],
                    "email": invite_email,
                    "password": "concurrent-invitee-password-456",
                    "name": "Second caregiver",
                },
            )
            assert accepted.status_code == 201, accepted.text
            invited_caregiver_id = accepted.json()["caregiver_id"]
            invited_headers = {"Authorization": f"Bearer {accepted.json()['access_token']}"}

        def write_evidence(headers, caregiver_id, label):
            payload = {
                "person_id": person_id,
                "type": "note",
                "evidence_kind": "caregiver_report",
                "epistemic_status": "caregiver_reported",
                "source_text": f"{label}: the room was quiet after lunch.",
                "source_type": "caregiver_report",
                "uploaded_by_caregiver_id": caregiver_id,
                "reported_by_caregiver_id": caregiver_id,
                "collected_by_caregiver_id": caregiver_id,
                "reported_by_label": label,
                "original_author": label,
                "time_provenance": "caregiver_reported",
                "ingestion_key": f"concurrent-{label.lower()}-{suffix}",
            }
            with TestClient(app) as request_client:
                return request_client.post("/api/v1/evidence", headers=headers, json=payload)

        with ThreadPoolExecutor(max_workers=2) as executor:
            first_future = executor.submit(write_evidence, primary_headers, primary_caregiver_id, "Primary")
            second_future = executor.submit(write_evidence, invited_headers, invited_caregiver_id, "Second")
            first = first_future.result(timeout=45)
            second = second_future.result(timeout=45)

        assert first.status_code == 200, first.text
        assert second.status_code == 200, second.text
        evidence_ids = [first.json()["id"], second.json()["id"]]
        assert evidence_ids[0] != evidence_ids[1]

        with TestClient(app) as client:
            shared_read = client.get(
                f"/api/v1/evidence/{person_id}",
                headers=invited_headers,
            )
            assert shared_read.status_code == 200, shared_read.text
            visible_ids = {item["id"] for item in shared_read.json()}
            assert set(evidence_ids).issubset(visible_ids)

        with Session() as db:
            rows = db.query(Evidence).filter(
                Evidence.person_id == person_id,
                Evidence.id.in_(evidence_ids),
            ).all()
            assert len(rows) == 2
            assert {row.uploaded_by_caregiver_id for row in rows} == {
                primary_caregiver_id,
                invited_caregiver_id,
            }
            assert {row.reported_by_caregiver_id for row in rows} == {
                primary_caregiver_id,
                invited_caregiver_id,
            }
    finally:
        if Session and person_id:
            with Session() as db:
                db.query(CareDeltaRun).filter_by(person_id=person_id).delete(synchronize_session=False)
                db.query(CareStateRevision).filter_by(person_id=person_id).delete(synchronize_session=False)
                db.query(AuditLogEvent).filter_by(person_id=person_id).delete(synchronize_session=False)
                db.query(Evidence).filter_by(person_id=person_id).delete(synchronize_session=False)
                db.query(CaregiverInvitation).filter_by(person_id=person_id).delete(synchronize_session=False)
                db.query(CaregiverAccessGrant).filter_by(person_id=person_id).delete(synchronize_session=False)
                db.query(AnonymousSession).filter_by(person_id=person_id).delete(synchronize_session=False)
                db.query(CaregiverAccount).filter_by(person_id=person_id).delete(synchronize_session=False)
                db.query(CaregiverProfile).filter_by(person_id=person_id).delete(synchronize_session=False)
                db.query(Caregiver).filter_by(person_id=person_id).delete(synchronize_session=False)
                db.query(Person).filter_by(id=person_id).delete(synchronize_session=False)
                db.commit()



def test_postgres_responsibility_transfer_requires_acceptance_and_preserves_history():
    suffix = uuid.uuid4().hex[:10]
    invite_email = f"responsibility-{suffix}@example.test"
    person_id = None

    try:
        with TestClient(app) as client:
            primary = client.post(
                "/api/v1/sessions/anonymous",
                json={"caregiver_name": f"Primary {suffix}"},
            )
            assert primary.status_code == 201, primary.text
            primary_body = primary.json()
            person_id = primary_body["person"]["id"]
            primary_headers = {"Authorization": f"Bearer {primary_body['access_token']}"}

            account = client.post(
                "/api/v1/accounts",
                headers=primary_headers,
                json={"email": invite_email, "password": "responsibility-primary-password-456"},
            )
            assert account.status_code == 201, account.text
            invitation = client.post(
                "/api/v1/caregiver-access/invitations",
                headers=primary_headers,
                json={"email": invite_email, "expires_in_days": 7},
            )
            assert invitation.status_code == 201, invitation.text
            accepted = client.post(
                "/api/v1/caregiver-access/invitations/accept",
                json={
                    "token": invitation.json()["invitation_token"],
                    "email": invite_email,
                    "password": "responsibility-recipient-password-456",
                    "name": "Second caregiver",
                },
            )
            assert accepted.status_code == 201, accepted.text
            recipient = accepted.json()
            recipient_headers = {"Authorization": f"Bearer {recipient['access_token']}"}

            loop = client.post(
                "/api/v1/open-loops",
                headers=recipient_headers,
                json={
                    "loop_type": "follow_up",
                    "title": f"Confirm appointment {suffix}",
                    "description": "Confirm the appointment with the practice.",
                },
            )
            assert loop.status_code == 201, loop.text
            loop_id = loop.json()["id"]

            responsibility = client.post(
                "/api/v1/caregiver-responsibilities",
                headers=recipient_headers,
                json={
                    "person_id": person_id,
                    "caregiver_id": recipient["caregiver_profile_id"],
                    "responsibility_type": "clinic_follow_up",
                    "scope": "Confirm the appointment with the practice",
                    "effective_start": "2026-11-20T09:00:00+00:00",
                    "status": "active",
                    "acceptance_status": "explicit",
                    "assignment_source": "caregiver_confirmed",
                },
            )
            assert responsibility.status_code == 200, responsibility.text
            source_id = responsibility.json()["id"]

            transfer = client.post(
                "/api/v1/caregiver-access/responsibility-transfers",
                headers=recipient_headers,
                json={
                    "source_responsibility_id": source_id,
                    "to_caregiver_profile_id": primary_body["caregiver"]["profile_id"],
                    "related_record_type": "open_loop",
                    "related_record_id": loop_id,
                    "reason": "The recipient accepted ongoing appointment coordination.",
                },
            )
            assert transfer.status_code == 201, transfer.text
            transfer_id = transfer.json()["id"]

            pending = client.get(
                "/api/v1/caregiver-access/responsibility-transfers",
                headers=primary_headers,
            )
            assert pending.status_code == 200, pending.text
            pending_item = next(item for item in pending.json() if item["id"] == transfer_id)
            assert pending_item["status"] == "pending"

            before_acceptance = client.get("/api/v1/open-loops", headers=primary_headers)
            assert before_acceptance.status_code == 200, before_acceptance.text
            assert not any(item["id"] == loop_id for item in before_acceptance.json()["items"])

            with Session() as db:
                original = db.query(CaregiverResponsibility).filter_by(id=source_id).one()
                assert original.status.value == "active"
                assert original.caregiver_id == recipient["caregiver_profile_id"]

            accepted_transfer = client.post(
                f"/api/v1/caregiver-access/responsibility-transfers/{transfer_id}/accept",
                headers=primary_headers,
            )
            assert accepted_transfer.status_code == 200, accepted_transfer.text
            assert accepted_transfer.json()["accepted"] is True

            after_acceptance = client.get("/api/v1/open-loops", headers=primary_headers)
            assert after_acceptance.status_code == 200, after_acceptance.text
            assigned_loop = next(item for item in after_acceptance.json()["items"] if item["id"] == loop_id)
            assert assigned_loop["assigned_caregiver_id"] == primary_body["caregiver"]["id"]
            assert assigned_loop["caregiver_id"] == recipient["caregiver_id"]

            with Session() as db:
                original = db.query(CaregiverResponsibility).filter_by(id=source_id).one()
                replacement = db.query(CaregiverResponsibility).filter_by(
                    id=accepted_transfer.json()["new_responsibility_id"]
                ).one()
                stored_transfer = db.query(CaregiverResponsibilityTransfer).filter_by(id=transfer_id).one()
                stored_handoff = db.query(CaregiverHandoff).filter(
                    CaregiverHandoff.person_id == person_id,
                    CaregiverHandoff.provenance == "caregiver_access.responsibility_transfer:" + transfer_id,
                ).one()
                assert original.status.value == "historical"
                assert original.effective_end is not None
                assert replacement.status.value == "active"
                assert replacement.caregiver_id == primary_body["caregiver"]["profile_id"]
                assert stored_transfer.status == "accepted"
                assert stored_handoff.acceptance_status.value == "accepted"
                assert stored_handoff.from_caregiver_id == recipient["caregiver_profile_id"]
                assert stored_handoff.to_caregiver_id == primary_body["caregiver"]["profile_id"]
                assert stored_handoff.is_completed is False
    finally:
        if Session and person_id:
            with Session() as db:
                db.query(CaregiverResponsibilityTransfer).filter_by(person_id=person_id).delete(synchronize_session=False)
                db.query(CaregiverHandoff).filter_by(person_id=person_id).delete(synchronize_session=False)
                db.query(CareStateRevision).filter_by(person_id=person_id).delete(synchronize_session=False)
                db.query(CaregiverResponsibility).filter_by(person_id=person_id).delete(synchronize_session=False)
                db.query(OpenLoopOccurrence).filter_by(person_id=person_id).delete(synchronize_session=False)
                db.query(OpenLoopEvent).filter_by(person_id=person_id).delete(synchronize_session=False)
                db.query(OpenLoopRecord).filter_by(person_id=person_id).delete(synchronize_session=False)
                db.query(AuditLogEvent).filter_by(person_id=person_id).delete(synchronize_session=False)
                db.query(CaregiverInvitation).filter_by(person_id=person_id).delete(synchronize_session=False)
                db.query(CaregiverAccessGrant).filter_by(person_id=person_id).delete(synchronize_session=False)
                db.query(AnonymousSession).filter_by(person_id=person_id).delete(synchronize_session=False)
                db.query(CaregiverAccount).filter_by(person_id=person_id).delete(synchronize_session=False)
                db.query(CaregiverProfile).filter_by(person_id=person_id).delete(synchronize_session=False)
                db.query(Caregiver).filter_by(person_id=person_id).delete(synchronize_session=False)
                db.query(Person).filter_by(id=person_id).delete(synchronize_session=False)
                db.commit()
