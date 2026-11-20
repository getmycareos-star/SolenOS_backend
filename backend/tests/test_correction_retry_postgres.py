"""Real PostgreSQL correction receipt and retry contract.

The test uses the migrated PostgreSQL schema and real HTTP middleware/routes.
Only the expensive downstream coordinator is fault-injected: the first attempt
fails after the correction receipt commits; the retry must recover that same
receipt, not create another correction.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api import v1 as api_v1
from app.main import app
from app.models.anonymous_session import AnonymousSession
from app.models.care import CareEvent, Caregiver, Correction, Person


def test_postgres_correction_receipt_is_durable_idempotent_retryable_and_scoped(monkeypatch):
    from app.services.learning import LearningEngine

    database_url = api_v1.get_settings().database_url if hasattr(api_v1, "get_settings") else None
    # The test runner supplies DATABASE_URL; use the same migrated PostgreSQL
    # database as the other integration checks.
    import os
    database_url = os.environ["DATABASE_URL"]
    if database_url.startswith("postgresql+psycopg://"):
        database_url = database_url.replace("postgresql+psycopg://", "postgresql+psycopg2://", 1)
    engine = create_engine(database_url, pool_pre_ping=True)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    suffix = uuid.uuid4().hex
    person_id = f"correction-pg-person-{suffix}"
    caregiver_id = f"correction-pg-caregiver-{suffix}"
    foreign_person_id = f"correction-pg-foreign-person-{suffix}"
    foreign_caregiver_id = f"correction-pg-foreign-caregiver-{suffix}"
    event_id = f"correction-pg-event-{suffix}"
    raw_token = f"correction-pg-token-{uuid.uuid4().hex}"
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    calls = {"reconciliation": 0}

    def fail_first_reconciliation(*args, **kwargs):
        calls["reconciliation"] += 1
        if calls["reconciliation"] == 1:
            raise RuntimeError("injected_postgres_reconciliation_failure")
        return {
            "workflow": "care_record_correction",
            "stages": {"reconciliation": "committed"},
            "transaction_owner": "caller",
        }

    # Keep this test focused on the persisted retry boundary. The correction
    # application itself is covered by the domain tests; this test proves the
    # HTTP/SQL transaction and recovery contract on PostgreSQL.
    monkeypatch.setattr(LearningEngine, "apply_correction", lambda self, correction, commit=True: None)
    monkeypatch.setattr(LearningEngine, "record_learning_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(LearningEngine, "after_save", lambda *args, **kwargs: None)
    monkeypatch.setattr(api_v1, "execute_care_continuity_reconciliation", fail_first_reconciliation)

    try:
        with Session() as db:
            db.add_all([
                Person(id=person_id, name="Correction retry integration person"),
                Person(id=foreign_person_id, name="Foreign correction integration person"),
            ])
            db.flush()
            db.add_all([
                Caregiver(id=caregiver_id, name="Correction caregiver", relationship="family", person_id=person_id),
                Caregiver(id=foreign_caregiver_id, name="Foreign caregiver", relationship="family", person_id=foreign_person_id),
            ])
            db.flush()
            db.add(CareEvent(
                id=event_id,
                person_id=person_id,
                event_type="caregiver_report",
                status="recorded",
                occurred_at=datetime.now(timezone.utc),
                title="Original caregiver report",
                description="Original report text remains unchanged.",
                evidence_ids=[],
                created_by_caregiver_id=caregiver_id,
            ))
            db.add(AnonymousSession(
                person_id=person_id,
                caregiver_id=caregiver_id,
                token_hash=token_hash,
                expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            ))
            db.add(AnonymousSession(
                person_id=foreign_person_id,
                caregiver_id=foreign_caregiver_id,
                token_hash=hashlib.sha256((raw_token + "-foreign").encode("utf-8")).hexdigest(),
                expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            ))
            db.commit()

        client = TestClient(app)
        headers = {"Authorization": f"Bearer {raw_token}"}
        payload = {
            "person_id": person_id,
            "caregiver_id": caregiver_id,
            "target_type": "care_event",
            "target_id": event_id,
            "original_text": "Original report text remains unchanged.",
            "corrected_text": "Caregiver clarifies that the original report was uncertain.",
            "reason": "Correction retry integration test",
            "idempotency_key": f"correction-pg-idempotency-{suffix}",
        }

        first = client.post("/api/v1/corrections", headers=headers, json=payload)
        assert first.status_code == 200, first.text
        first_body = first.json()
        correction_id = first_body["id"]
        assert first_body["processing_status"] == "pending"
        assert first_body["processing_attempts"] == 1
        assert first_body["care_continuity_execution"]["retry_required"] is True
        assert first_body["care_continuity_execution"]["stages"]["reconciliation"] == "pending"

        # Close/reopen the SQLAlchemy session: the pending state must be in
        # PostgreSQL, not merely attached to the request's ORM identity map.
        with Session() as db:
            persisted = db.query(Correction).filter(
                Correction.id == correction_id,
                Correction.person_id == person_id,
            ).one()
            assert persisted.processing_status == "pending"
            assert persisted.processing_attempts == 1
            assert persisted.next_retry_at is not None
            original = db.query(CareEvent).filter(CareEvent.id == event_id).one()
            assert original.description == "Original report text remains unchanged."

        retry = client.post(
            f"/api/v1/corrections/{person_id}/{correction_id}/retry",
            headers=headers,
        )
        assert retry.status_code == 200, retry.text
        retry_body = retry.json()
        assert retry_body["id"] == correction_id
        assert retry_body["processing_status"] == "completed"
        assert retry_body["processing_attempts"] == 2
        assert retry_body["processed_at"] is not None
        assert retry_body["care_continuity_execution"]["retry_required"] is False
        assert calls["reconciliation"] == 2

        # Replaying the same request returns the original correction and cannot
        # repeat reconciliation or create another correction row.
        replay = client.post("/api/v1/corrections", headers=headers, json=payload)
        assert replay.status_code == 200, replay.text
        assert replay.json()["id"] == correction_id
        assert replay.json()["processing_attempts"] == 2
        assert calls["reconciliation"] == 2

        changed_payload = {**payload, "corrected_text": "Different correction text."}
        conflict = client.post("/api/v1/corrections", headers=headers, json=changed_payload)
        assert conflict.status_code == 409, conflict.text

        # A different caregiver/person capability cannot inspect or retry this
        # correction, even when it knows the correction and person identifiers.
        foreign_token = raw_token + "-foreign"
        foreign_headers = {"Authorization": f"Bearer {foreign_token}"}
        foreign_read = client.get(f"/api/v1/corrections/{person_id}", headers=foreign_headers)
        assert foreign_read.status_code == 403, foreign_read.text
        foreign_retry = client.post(
            f"/api/v1/corrections/{person_id}/{correction_id}/retry",
            headers=foreign_headers,
        )
        assert foreign_retry.status_code == 403, foreign_retry.text

        with Session() as db:
            rows = db.query(Correction).filter(Correction.person_id == person_id).all()
            assert len(rows) == 1
            assert rows[0].id == correction_id
            assert rows[0].processing_status == "completed"
            assert db.query(CareEvent).filter(
                CareEvent.id == event_id,
                CareEvent.description == "Original report text remains unchanged.",
            ).count() == 1
    finally:
        with Session() as db:
            db.query(Correction).filter(Correction.person_id == person_id).delete(synchronize_session=False)
            db.query(CareEvent).filter(CareEvent.id == event_id).delete(synchronize_session=False)
            db.query(AnonymousSession).filter(AnonymousSession.person_id.in_([person_id, foreign_person_id])).delete(synchronize_session=False)
            db.query(Caregiver).filter(Caregiver.person_id.in_([person_id, foreign_person_id])).delete(synchronize_session=False)
            db.query(Person).filter(Person.id.in_([person_id, foreign_person_id])).delete(synchronize_session=False)
            db.commit()
        engine.dispose()
