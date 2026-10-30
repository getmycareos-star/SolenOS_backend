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

from app.core.database import Base
from app.models.care import Caregiver, CareEvent, Person
from app.models.change import Baseline, Change, Pattern
from app.models.significance import AssessmentDimension, SignificanceAssessment
from app.models.care_continuity import CareStateRevision
from app.services.change_intelligence_reconciliation import reconcile_person_change_intelligence

DATABASE_URL = os.environ.get("DATABASE_URL", "")
# CI may supply psycopg while this test environment installs psycopg2-binary.
# Normalize only the driver prefix; preserve the connection target and credentials.
if DATABASE_URL.startswith("postgresql+psycopg://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql+psycopg://", "postgresql+psycopg2://", 1)
pytestmark = pytest.mark.skipif(
    not DATABASE_URL.startswith(("postgresql://", "postgresql+psycopg2://")),
    reason="requires a real PostgreSQL DATABASE_URL",
)
REFERENCE_TIME = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)


def test_postgres_pattern_persistence_late_evidence_correction_and_person_isolation():
    engine = create_engine(DATABASE_URL, pool_pre_ping=True)
    Base.metadata.create_all(bind=engine)
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
        # Adversarial duplicate: importer creates a second row for the same
        # source anchor at the exact same occurrence time. It must not inflate
        # recurrence or appear as a second canonical source event.
        a1_duplicate = add_event(person_a_id, caregiver_a_id, f"pg-event-a1-zduplicate-{suffix}", 20, f"pg-source-a1-{suffix}")
        a2 = add_event(person_a_id, caregiver_a_id, f"pg-event-a2-{suffix}", 10, f"pg-source-a2-{suffix}")
        # Same evidence anchor at a different time is retained as a distinct
        # occurrence, but remains one shared source (not independent corroboration).
        a2_shared_source = add_event(person_a_id, caregiver_a_id, f"pg-event-a2-shared-{suffix}", 9, f"pg-source-a2-{suffix}")
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
        # The exact duplicate is excluded from canonical recurrence evidence.
        assert all(a1_duplicate.id not in (pattern.event_ids or []) for pattern in first_a)
        # Unknown coverage is not converted into an apparent frequency change.
        assert all(pattern.pattern_type.value not in {"frequency_increase", "frequency_decrease"} for pattern in first_a)
        # Two differently dated event records may represent separate occurrences,
        # but their shared evidence anchor must not be described as independent
        # corroboration. Pattern event_ids remain event IDs, not evidence IDs.
        assert a2.evidence_ids == a2_shared_source.evidence_ids
        assert any(
            a2.id in (pattern.event_ids or []) and a2_shared_source.id in (pattern.event_ids or [])
            for pattern in first_a
        )

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
        # Historical reconstruction: the old assessment remains inspectable,
        # explicitly inactive, with the now-invalidated source lineage intact.
        historical_with_corrected_source = db.query(Pattern).filter(
            Pattern.person_id == person_a_id,
            Pattern.is_active.is_(False),
        ).all()
        assert any(a3.id in (pattern.event_ids or []) for pattern in historical_with_corrected_source)
        assert db.query(Pattern).filter(Pattern.person_id == person_a_id).count() >= len(first_a_ids)
        assert all(pattern.person_id == person_b_id for pattern in db.query(Pattern).filter(
            Pattern.person_id == person_b_id
        ).all())
    finally:
        db.rollback()
        # Remove only this test's rows in reverse foreign-key order. Changes
        # reference significance assessments, so they must be removed before the
        # assessment rows; patterns also reference changes and must go first.
        db.query(Pattern).filter(
            Pattern.person_id.in_([person_a_id, person_b_id])
        ).delete(synchronize_session=False)
        db.query(Change).filter(
            Change.person_id.in_([person_a_id, person_b_id])
        ).delete(synchronize_session=False)
        assessment_ids = db.query(SignificanceAssessment.id).filter(
            SignificanceAssessment.person_id.in_([person_a_id, person_b_id])
        ).subquery()
        db.query(AssessmentDimension).filter(
            AssessmentDimension.assessment_id.in_(assessment_ids)
        ).delete(synchronize_session=False)
        db.query(SignificanceAssessment).filter(
            SignificanceAssessment.person_id.in_([person_a_id, person_b_id])
        ).delete(synchronize_session=False)
        for model in (Baseline, CareEvent, Caregiver):
            db.query(model).filter(model.person_id.in_([person_a_id, person_b_id])).delete(synchronize_session=False)
        # The audit listener records revisions for ORM writes, while this
        # fixture cleanup intentionally uses SQL bulk deletes. Explicitly delete
        # the fixture's revision rows and assert the FK dependency is gone before
        # deleting the person rows; this catches cleanup regressions at the source.
        from sqlalchemy import text
        db.execute(
            text("DELETE FROM care_state_revisions WHERE person_id IN (:person_a, :person_b)"),
            {"person_a": person_a_id, "person_b": person_b_id},
        )
        remaining_revisions = db.execute(
            text("SELECT count(*) FROM care_state_revisions WHERE person_id IN (:person_a, :person_b)"),
            {"person_a": person_a_id, "person_b": person_b_id},
        ).scalar_one()
        assert remaining_revisions == 0, "test fixture audit revisions must be removed before fixture persons"
        db.query(Person).filter(Person.id.in_([person_a_id, person_b_id])).delete(synchronize_session=False)
        db.commit()
        db.close()
        engine.dispose()


def test_postgres_concurrent_conflict_transitions_form_a_serial_audit_chain():
    """A disposable CI PostgreSQL database verifies row-locking under competing writers.

    The fixture rows are intentionally retained: conflict history has a database-enforced
    append-only policy, and this workflow's PostgreSQL service is discarded after the run.
    """
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from app.models.uncertainty import Contradiction, ContradictionResolutionEvent
    from app.schemas.uncertainty import ContradictionUpdate
    from app.services.uncertainty import UncertaintyEngine

    engine = create_engine(DATABASE_URL, pool_pre_ping=True)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    suffix = uuid.uuid4().hex
    person_id = f"conflict-concurrency-person-{suffix}"
    caregiver_id = f"conflict-concurrency-caregiver-{suffix}"
    conflict_id = f"conflict-concurrency-{suffix}"

    with Session() as db:
        db.add(Person(id=person_id, name="PostgreSQL concurrent conflict fixture"))
        db.flush()
        db.add(Caregiver(
            id=caregiver_id,
            name="PostgreSQL concurrent conflict caregiver",
            relationship="family",
            person_id=person_id,
        ))
        db.flush()
        db.add(Contradiction(
            id=conflict_id,
            person_id=person_id,
            subject_type="medication",
            subject_id=f"medication-{suffix}",
            field="status",
            description="Concurrent transition fixture; no clinical interpretation.",
            matching_candidate_ids=[],
            evidence_ids=[],
            resolution_status="unresolved",
        ))
        db.commit()

    start = Barrier(2)

    def transition(status, rationale):
        start.wait(timeout=10)
        with Session() as db:
            updated = UncertaintyEngine(
                db, person_id=person_id, caregiver_id=caregiver_id
            ).update_contradiction(
                conflict_id,
                ContradictionUpdate(
                    resolution_status=status,
                    resolution_notes=rationale,
                ),
            )
            assert updated is not None
            return updated.resolution_status

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(
                transition, "qualified",
                "Concurrent writer A preserved the conflict pending evidence review.",
            )
            second = pool.submit(
                transition, "reopened",
                "Concurrent writer B reopened the conflict for a new report.",
            )
            first.result(timeout=20)
            second.result(timeout=20)

        with Session() as db:
            conflict = db.query(Contradiction).filter(
                Contradiction.id == conflict_id,
                Contradiction.person_id == person_id,
            ).one()
            events = db.query(ContradictionResolutionEvent).filter(
                ContradictionResolutionEvent.contradiction_id == conflict_id,
                ContradictionResolutionEvent.person_id == person_id,
            ).all()

        assert len(events) == 2, "both concurrent state transitions must be audited"
        assert sum(event.previous_status == "unresolved" for event in events) == 1, (
            "exactly one transition may claim the original unresolved state"
        )
        first_event = next(event for event in events if event.previous_status == "unresolved")
        second_event = next(event for event in events if event.previous_status != "unresolved")
        assert second_event.previous_status == first_event.new_status, (
            "concurrent conflict audit events must form one serial state-transition chain"
        )
        assert second_event.new_status == conflict.resolution_status, (
            "persisted conflict state must match the final audited transition"
        )
    finally:
        engine.dispose()

