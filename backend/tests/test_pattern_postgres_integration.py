"""PostgreSQL integration proof for persisted change-pattern lifecycle.

This suite deliberately uses the same PostgreSQL service as the migration workflow.
It is not a SQLite substitute. All rows are test-scoped and cleaned up after the
assertions, including when an assertion fails.
"""
from datetime import datetime, timedelta, timezone
import os
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.care import Caregiver, CareEvent, Person
from app.models.change import Baseline, Change, Pattern
from app.services.change_intelligence_reconciliation import reconcile_person_change_intelligence

DATABASE_URL = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL.startswith(("postgresql://", "postgresql+psycopg2://")),
    reason="requires a real PostgreSQL DATABASE_URL",
)
REFERENCE_TIME = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)


def test_postgres_pattern_persistence_late_evidence_correction_and_person_isolation():
    engine = create_engine(DATABASE_URL, pool_pre_ping=True)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    suffix = uuid.uuid4().hex
    person_a_id = f"pattern-pg-a-{suffix}"
    person_b_id = f"pattern-pg-b-{suffix}"
    caregiver_a_id = f"pattern-pg-caregiver-a-{suffix}"
    caregiver_b_id = f"pattern-pg-caregiver-b-{suffix}"
    db = Session()
    try:
        db.add_all([
            Person(id=person_a_id, name="PostgreSQL integration fixture A"),
            Person(id=person_b_id, name="PostgreSQL integration fixture B"),
        ])
        db.flush()
        db.add_all([
            Caregiver(id=caregiver_a_id, name="Integration caregiver A", relationship="family", person_id=person_a_id),
            Caregiver(id=caregiver_b_id, name="Integration caregiver B", relationship="family", person_id=person_b_id),
        ])
        db.flush()

        def add_event(person_id, caregiver_id, event_id, days_ago, evidence_id):
            event = CareEvent(
                id=event_id,
                person_id=person_id,
                event_type="fall",
                status="recorded",
                occurred_at=REFERENCE_TIME - timedelta(days=days_ago),
                title="PostgreSQL integration fixture event",
                evidence_ids=[evidence_id],
                created_by_caregiver_id=caregiver_id,
            )
            db.add(event)
            db.flush()
            return event

        a1 = add_event(person_a_id, caregiver_a_id, f"pg-event-a1-{suffix}", 20, f"pg-source-a1-{suffix}")
        a2 = add_event(person_a_id, caregiver_a_id, f"pg-event-a2-{suffix}", 10, f"pg-source-a2-{suffix}")
        b1 = add_event(person_b_id, caregiver_b_id, f"pg-event-b1-{suffix}", 19, f"pg-source-b1-{suffix}")
        b2 = add_event(person_b_id, caregiver_b_id, f"pg-event-b2-{suffix}", 9, f"pg-source-b2-{suffix}")
        db.flush()

        reconcile_person_change_intelligence(db, person_a_id, REFERENCE_TIME)
        reconcile_person_change_intelligence(db, person_b_id, REFERENCE_TIME)
        db.commit()
        first_a = db.query(Pattern).filter(
            Pattern.person_id == person_a_id, Pattern.is_active.is_(True)
        ).all()
        first_b = db.query(Pattern).filter(
            Pattern.person_id == person_b_id, Pattern.is_active.is_(True)
        ).all()
        assert first_a and first_b
        first_a_ids = {pattern.id for pattern in first_a}
        assert first_a_ids
        assert all(pattern.person_id == person_a_id for pattern in first_a)
        assert all(pattern.person_id == person_b_id for pattern in first_b)
        assert not ({pattern.id for pattern in first_a} & {pattern.id for pattern in first_b})

        # A fresh SQLAlchemy session proves database persistence, not only identity-map state.
        db.close()
        db = Session()
        persisted = db.query(Pattern).filter(
            Pattern.person_id == person_a_id, Pattern.is_active.is_(True)
        ).all()
        assert {pattern.id for pattern in persisted} == first_a_ids

        # Late-arriving source event must be incorporated by explicit reconciliation.
        a3 = add_event(person_a_id, caregiver_a_id, f"pg-event-a3-{suffix}", 2, f"pg-source-a3-{suffix}")
        db.flush()
        reconcile_person_change_intelligence(db, person_a_id, REFERENCE_TIME)
        db.commit()
        after_late_event = db.query(Pattern).filter(
            Pattern.person_id == person_a_id, Pattern.is_active.is_(True)
        ).all()
        assert after_late_event
        assert all(pattern.person_id == person_a_id for pattern in after_late_event)
        assert any(a3.id in (pattern.event_ids or []) for pattern in after_late_event)

        # A correction invalidates the source event; reassessment must not keep it
        # in active pattern evidence. The historical Pattern row is retained.
        a3.status = "invalidated"
        db.flush()
        reconcile_person_change_intelligence(db, person_a_id, REFERENCE_TIME)
        db.commit()
        active_after_correction = db.query(Pattern).filter(
            Pattern.person_id == person_a_id, Pattern.is_active.is_(True)
        ).all()
        assert all(a3.id not in (pattern.event_ids or []) for pattern in active_after_correction)
        assert db.query(Pattern).filter(Pattern.person_id == person_a_id).count() >= len(first_a_ids)
        assert all(pattern.person_id == person_b_id for pattern in db.query(Pattern).filter(
            Pattern.person_id == person_b_id
        ).all())
    finally:
        db.rollback()
        # Remove only this test's rows. Delete dependent assessments before source records.
        for model in (Pattern, Change, Baseline, CareEvent, Caregiver):
            db.query(model).filter(model.person_id.in_([person_a_id, person_b_id])).delete(synchronize_session=False)
        db.query(Person).filter(Person.id.in_([person_a_id, person_b_id])).delete(synchronize_session=False)
        db.commit()
        db.close()
        engine.dispose()
