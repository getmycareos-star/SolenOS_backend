"""Real PostgreSQL correction receipt and retry contract.

The test uses the migrated PostgreSQL schema and real HTTP middleware/routes.
Only the expensive downstream coordinator is fault-injected: the first attempt
fails after the correction receipt commits; the retry must recover that same
receipt, not create another correction.
"""
from datetime import datetime, timedelta, timezone
import os
import hashlib
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import pytest

from app.api import v1 as api_v1
from app.main import app
from app.models.anonymous_session import AnonymousSession
from app.models.caregiver_access import CaregiverAccessGrant
from app.models.care import CareEvent, Caregiver, Correction, Person

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="requires the real PostgreSQL integration database",
)


def test_postgres_correction_receipt_is_durable_idempotent_retryable_and_scoped(monkeypatch):
    from app.services.learning import LearningEngine

    # The test runner supplies DATABASE_URL; use the same migrated PostgreSQL
    # database as the other integration checks.
    import os
    database_url = os.environ["DATABASE_URL"]
    if database_url.startswith("postgresql+psycopg://"):
        database_url = database_url.replace("postgresql+psycopg://", "postgresql+psycopg2://", 1)
    engine = create_engine(database_url, pool_pre_ping=True)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    suffix = uuid.uuid4().hex[:8]
    person_id = f"cpg-person-{suffix}"
    caregiver_id = f"cpg-caregiver-{suffix}"
    foreign_person_id = f"cpg-other-person-{suffix}"
    foreign_caregiver_id = f"cpg-other-caregiver-{suffix}"
    event_id = f"cpg-event-{suffix}"
    raw_token = f"correction-pg-token-{uuid.uuid4().hex}"
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    calls = {"reconciliation": 0}

    def fail_first_reconciliation(*args, **kwargs):
        calls["reconciliation"] += 1
        if calls["reconciliation"] == 1:
            # An empty/ambiguous result must not be mistaken for a committed
            # reconciliation merely because no explicit failure stage exists.
            return {"workflow": "care_record_correction", "stages": {}}
        return {
            "workflow": "care_record_correction",
            "stages": {"reconciliation": "staged_in_caller_transaction"},
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
            db.add(CaregiverAccessGrant(
                person_id=person_id,
                caregiver_id=caregiver_id,
                role="family",
                access_scope="full_record",
                status="active",
                permissions=["view_lcr", "add_evidence", "correct_records"],
            ))
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
            persisted.next_retry_at = datetime.now(timezone.utc) + timedelta(hours=1)
            original = db.query(CareEvent).filter(CareEvent.id == event_id).one()
            assert original.description == "Original report text remains unchanged."
            db.commit()

        # An identical POST is idempotent but must not bypass the persisted
        # backoff. The explicit retry endpoint is the deliberate override.
        pending_replay = client.post("/api/v1/corrections", headers=headers, json=payload)
        assert pending_replay.status_code == 200, pending_replay.text
        assert pending_replay.json()["processing_status"] == "pending"
        assert pending_replay.json()["processing_attempts"] == 1
        assert calls["reconciliation"] == 1

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
            # CareStateRevision is an append-only audit ledger with a restrictive
            # person FK. Keep these uniquely named CI fixture parents rather than
            # deleting a person and violating the immutable history contract.
            db.commit()
        engine.dispose()


def test_postgres_delta_replay_is_invalidated_by_a_later_correction():
    """Repeated identical input reuses one run; a later correction forces a new comparison."""
    from app.models.care_continuity import CareDeltaRun
    from app.models.anonymous_session import AnonymousSession
    from app.models.care import CareEvent, Caregiver, Correction, Person

    import os
    database_url = os.environ["DATABASE_URL"]
    if database_url.startswith("postgresql+psycopg://"):
        database_url = database_url.replace("postgresql+psycopg://", "postgresql+psycopg2://", 1)
    engine = create_engine(database_url, pool_pre_ping=True)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    suffix = uuid.uuid4().hex[:8]
    person_id = f"dpg-person-{suffix}"
    caregiver_id = f"dpg-caregiver-{suffix}"
    raw_token = f"delta-replay-token-{uuid.uuid4().hex}"
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    event_id = f"dpg-event-{suffix}"
    source_text = "Caregiver reports that overnight waking has increased."
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {raw_token}"}

    with Session() as db:
        db.add(Person(id=person_id, name="Delta replay integration person"))
        db.flush()
        db.add(Caregiver(
            id=caregiver_id,
            name="Delta replay caregiver",
            relationship="family",
            person_id=person_id,
        ))
        db.flush()
        db.add(CaregiverAccessGrant(
            person_id=person_id,
            caregiver_id=caregiver_id,
            role="family",
            access_scope="full_record",
            status="active",
            permissions=["view_lcr", "add_evidence", "correct_records"],
        ))
        event = CareEvent(
            id=event_id,
            person_id=person_id,
            event_type="caregiver_report",
            status="recorded",
            occurred_at=datetime.now(timezone.utc) - timedelta(days=1),
            title="Overnight waking report",
            description=source_text,
            evidence_ids=[],
            created_by_caregiver_id=caregiver_id,
        )
        db.add(event)
        db.add(AnonymousSession(
            person_id=person_id,
            caregiver_id=caregiver_id,
            token_hash=token_hash,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        ))
        db.commit()
        event_date = event.occurred_at.isoformat()

    payload = {
        "person_id": person_id,
        "event_date": event_date,
        "source": "postgres_longitudinal_replay_acceptance",
        "content": source_text,
        "source_event_id": event_id,
        "evidence_ids": [],
        "persist": True,
    }
    first = client.post("/api/v1/care-delta/reconcile", headers=headers, json=payload)
    assert first.status_code == 200, first.text
    first_id = first.json()["provenance"]["delta_run_id"]

    replay = client.post("/api/v1/care-delta/reconcile", headers=headers, json=payload)
    assert replay.status_code == 200, replay.text
    assert replay.json()["provenance"]["delta_run_id"] == first_id

    with Session() as db:
        assert db.query(CareDeltaRun).filter(
            CareDeltaRun.person_id == person_id,
            CareDeltaRun.source_event_id == event_id,
            CareDeltaRun.source == payload["source"],
        ).count() == 1

    correction = client.post("/api/v1/corrections", headers=headers, json={
        "person_id": person_id,
        "caregiver_id": caregiver_id,
        "target_type": "care_event",
        "target_id": event_id,
        "original_text": source_text,
        "corrected_text": "Caregiver corrects the report: the frequency was not confirmed.",
        "reason": "Later clarification of the original report.",
        "idempotency_key": f"delta-replay-correction-{suffix}",
    })
    assert correction.status_code == 200, correction.text
    assert correction.json()["processing_status"] == "completed"

    # The correction is append-only: original source text remains intact. Its
    # revision changes the comparison basis, so the earlier Delta run is stale.
    runs_response = client.get(f"/api/v1/care-delta/runs/{person_id}", headers=headers)
    assert runs_response.status_code == 200, runs_response.text
    prior = next(item for item in runs_response.json() if item["id"] == first_id)
    assert prior["source_freshness"]["status"] == "stale"
    assert prior["source_freshness"]["changed_context_count"] > 0

    refreshed = client.post("/api/v1/care-delta/reconcile", headers=headers, json=payload)
    assert refreshed.status_code == 200, refreshed.text
    second_id = refreshed.json()["provenance"]["delta_run_id"]
    assert second_id != first_id, "a corrected care record must not reuse a Delta run from the earlier comparison basis"

    with Session() as db:
        assert db.query(CareDeltaRun).filter(
            CareDeltaRun.person_id == person_id,
            CareDeltaRun.source_event_id == event_id,
            CareDeltaRun.source == payload["source"],
        ).count() == 2
        original = db.query(CareEvent).filter(CareEvent.id == event_id).one()
        assert original.description == source_text
        assert db.query(Correction).filter(
            Correction.person_id == person_id,
            Correction.target_id == event_id,
        ).count() == 1
    engine.dispose()


def test_postgres_correction_recovery_loop_reclaims_pending_and_expired_processing_rows(monkeypatch):
    """A fresh app worker can recover durable correction receipts after restart."""
    from app.main import process_pending_corrections_once
    from app.services.learning import LearningEngine

    database_url = os.environ["DATABASE_URL"]
    if database_url.startswith("postgresql+psycopg://"):
        database_url = database_url.replace("postgresql+psycopg://", "postgresql+psycopg2://", 1)
    engine = create_engine(database_url, pool_pre_ping=True)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    suffix = uuid.uuid4().hex[:8]
    person_id = f"recover-person-{suffix}"
    caregiver_id = f"recover-caregiver-{suffix}"
    pending_id = f"recover-pending-{suffix}"
    stale_id = f"recover-stale-{suffix}"
    old_time = datetime(2000, 1, 1, tzinfo=timezone.utc)

    monkeypatch.setattr(LearningEngine, "apply_correction", lambda self, correction, commit=True: None)
    monkeypatch.setattr(LearningEngine, "record_learning_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(LearningEngine, "after_save", lambda *args, **kwargs: None)
    monkeypatch.setattr(api_v1, "_reassess_correction_follow_ups", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        api_v1,
        "execute_care_continuity_reconciliation",
        lambda *args, **kwargs: {
            "workflow": "care_record_correction",
            "stages": {"reconciliation": "staged_in_caller_transaction"},
            "transaction_owner": "caller",
        },
    )

    try:
        with Session() as db:
            db.add(Person(id=person_id, name="Correction recovery integration person"))
            db.flush()
            db.add(Caregiver(
                id=caregiver_id,
                name="Correction recovery caregiver",
                relationship="family",
                person_id=person_id,
            ))
            db.flush()
            db.add_all([
                Correction(
                    id=pending_id,
                    person_id=person_id,
                    target_type="care_event",
                    target_id="pending-target",
                    original_text="Pending original",
                    corrected_text="Pending corrected",
                    reason="Recovery test",
                    caregiver_id=caregiver_id,
                    idempotency_key=f"pending-{suffix}",
                    request_sha256=hashlib.sha256(f"pending-{suffix}".encode()).hexdigest(),
                    processing_status="pending",
                    processing_attempts=0,
                    next_retry_at=old_time,
                    created_at=old_time,
                    care_continuity_execution={"status": "pending", "retry_required": True},
                ),
                Correction(
                    id=stale_id,
                    person_id=person_id,
                    target_type="care_event",
                    target_id="stale-target",
                    original_text="Stale original",
                    corrected_text="Stale corrected",
                    reason="Recovery test",
                    caregiver_id=caregiver_id,
                    idempotency_key=f"stale-{suffix}",
                    request_sha256=hashlib.sha256(f"stale-{suffix}".encode()).hexdigest(),
                    processing_status="processing",
                    processing_attempts=1,
                    processing_started_at=old_time,
                    created_at=old_time,
                    care_continuity_execution={"status": "processing", "retry_required": True},
                ),
            ])
            db.commit()

        # This opens fresh database sessions and exercises the same recovery
        # entry point used by the application's lifespan worker after restart.
        result = process_pending_corrections_once(batch_size=1)
        assert result == {"examined": 2, "completed": 2, "pending": 0, "failed": 0}

        with Session() as db:
            pending = db.query(Correction).filter_by(id=pending_id, person_id=person_id).one()
            stale = db.query(Correction).filter_by(id=stale_id, person_id=person_id).one()
            assert pending.processing_status == "completed"
            assert pending.processing_attempts == 1
            assert pending.processed_at is not None
            assert stale.processing_status == "completed"
            assert stale.processing_attempts == 2
            assert stale.processed_at is not None
    finally:
        with Session() as db:
            db.query(Correction).filter(Correction.id.in_([pending_id, stale_id])).delete(synchronize_session=False)
            db.query(Caregiver).filter_by(id=caregiver_id).delete(synchronize_session=False)
            db.query(Person).filter_by(id=person_id).delete(synchronize_session=False)
            db.commit()
        engine.dispose()
