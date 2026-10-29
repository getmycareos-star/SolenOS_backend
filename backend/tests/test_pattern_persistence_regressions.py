"""Database-backed pattern lifecycle regression tests."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models.care import Person, Caregiver, CareEvent
from app.models.change import Pattern
from app.services.pattern_detection import detect_patterns

REFERENCE_TIME = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)
engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def seed_person(db, suffix):
    person = Person(id=f"persist-person-{suffix}", name="Synthetic fixture")
    caregiver = Caregiver(id=f"persist-caregiver-{suffix}", name="Synthetic caregiver", relationship="family", person_id=person.id)
    db.add_all([person, caregiver])
    db.flush()
    for event_id, days in ((f"event-a-{suffix}", 30), (f"event-b-{suffix}", 12), (f"event-c-{suffix}", 3)):
        db.add(CareEvent(id=event_id, person_id=person.id, event_type="fall", status="recorded",
                         occurred_at=REFERENCE_TIME - timedelta(days=days), title="Synthetic event",
                         evidence_ids=[], created_by_caregiver_id=caregiver.id))
    db.flush()
    return person.id


def test_patterns_survive_new_session_and_reassessment_is_idempotent():
    Base.metadata.create_all(bind=engine)
    try:
        with Session() as db:
            person_id = seed_person(db, "restart")
            first = detect_patterns(db, person_id, "fall", None, REFERENCE_TIME)
            db.commit()
            first_ids = {p.id for p in first}
            assert first_ids
        with Session() as restarted:
            before = {p.id for p in restarted.query(Pattern).filter_by(person_id=person_id, is_active=True).all()}
            assert before == first_ids
            second = detect_patterns(restarted, person_id, "fall", None, REFERENCE_TIME)
            restarted.commit()
            after = {p.id for p in restarted.query(Pattern).filter_by(person_id=person_id, is_active=True).all()}
            assert {p.id for p in second} == first_ids
            assert after == first_ids
    finally:
        Base.metadata.drop_all(bind=engine)


def test_invalidated_source_retires_pattern_without_deleting_history():
    Base.metadata.create_all(bind=engine)
    try:
        with Session() as db:
            person_id = seed_person(db, "correction")
            patterns = detect_patterns(db, person_id, "fall", None, REFERENCE_TIME)
            db.commit()
            original_ids = {p.id for p in patterns}
            assert original_ids
            db.query(CareEvent).filter_by(person_id=person_id, event_type="fall").update(
                {"status": "invalidated"}, synchronize_session=False
            )
            db.flush()
            detect_patterns(db, person_id, "fall", None, REFERENCE_TIME)
            db.commit()
            rows = db.query(Pattern).filter_by(person_id=person_id).all()
            assert original_ids <= {row.id for row in rows}
            assert all(not row.is_active for row in rows)
    finally:
        Base.metadata.drop_all(bind=engine)


def test_pattern_reconciliation_isolated_between_people():
    Base.metadata.create_all(bind=engine)
    try:
        with Session() as db:
            person_a = seed_person(db, "isolation-a")
            person_b = seed_person(db, "isolation-b")
            patterns_a = detect_patterns(db, person_a, "fall", None, REFERENCE_TIME)
            patterns_b = detect_patterns(db, person_b, "fall", None, REFERENCE_TIME)
            db.commit()
            assert patterns_a and patterns_b
            assert all(p.person_id == person_a for p in patterns_a)
            assert all(p.person_id == person_b for p in patterns_b)
            assert not ({p.id for p in patterns_a} & {p.id for p in patterns_b})
    finally:
        Base.metadata.drop_all(bind=engine)
