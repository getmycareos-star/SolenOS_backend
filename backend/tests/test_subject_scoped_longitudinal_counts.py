"""Regression tests for subject-scoped longitudinal event populations."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.core.change_enums import ChangeType, ChangeDirection
from app.models.care import Person, Caregiver, CareEvent
from app.models.change import Change
from app.services.baseline import get_longitudinal_event_counts


def test_subject_id_is_resolved_through_source_links_not_treated_as_event_id():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    db = session_factory()
    now = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)
    try:
        person = Person(id="scope-person", name="Fixture person")
        caregiver = Caregiver(
            id="scope-caregiver", name="Fixture caregiver",
            relationship="family", person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.flush()

        selected = CareEvent(
            id="source-selected", person_id=person.id, event_type="fall",
            status="recorded", occurred_at=now - timedelta(days=3),
            title="Selected source event", evidence_ids=["evidence-selected"],
            created_by_caregiver_id=caregiver.id,
        )
        unrelated = CareEvent(
            id="source-unrelated", person_id=person.id, event_type="fall",
            status="recorded", occurred_at=now - timedelta(days=2),
            title="Unrelated source event", evidence_ids=["evidence-unrelated"],
            created_by_caregiver_id=caregiver.id,
        )
        db.add_all([selected, unrelated])
        db.flush()

        db.add_all([
            Change(
                id="change-selected", person_id=person.id, subject_type="fall",
                subject_id="subject-A", change_type=ChangeType.RECURRENCE,
                change_direction=ChangeDirection.INCREASING,
                evidence_ids=[selected.id], confirmation_status="detected",
                is_active=True,
            ),
            Change(
                id="change-other-subject", person_id=person.id, subject_type="fall",
                subject_id="subject-B", change_type=ChangeType.RECURRENCE,
                change_direction=ChangeDirection.INCREASING,
                evidence_ids=[unrelated.id], confirmation_status="detected",
                is_active=True,
            ),
        ])
        db.flush()

        counts = get_longitudinal_event_counts(
            db, person.id, "fall", "subject-A", now
        )

        assert [event.id for event in counts["all_events"]] == [selected.id]
        assert counts["current_window_count"] == 1
        assert counts["total_events"] == 1
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_subject_scope_without_source_links_does_not_fall_back_to_unrelated_events():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    db = session_factory()
    now = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)
    try:
        person = Person(id="empty-scope-person", name="Fixture person")
        caregiver = Caregiver(
            id="empty-scope-caregiver", name="Fixture caregiver",
            relationship="family", person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.flush()
        db.add(CareEvent(
            id="unlinked-event", person_id=person.id, event_type="fall",
            status="recorded", occurred_at=now - timedelta(days=1),
            title="Unlinked event", evidence_ids=[],
            created_by_caregiver_id=caregiver.id,
        ))
        db.flush()

        counts = get_longitudinal_event_counts(
            db, person.id, "fall", "subject-with-no-links", now
        )

        assert counts["all_events"] == []
        assert counts["current_window_count"] == 0
        assert counts["history_truncated"] is False
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()
