from datetime import datetime, timedelta, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models.care import Person, Caregiver
from app.models.observation_coverage import ObservationCoverage
from app.services.observation_coverage import assess_observation_coverage


engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def _db():
    Base.metadata.create_all(bind=engine)
    db = Session()
    person = Person(id="coverage-person", name="Coverage test")
    caregiver = Caregiver(id="coverage-caregiver", name="Caregiver", relationship="family", person_id=person.id)
    db.add_all([person, caregiver])
    db.commit()
    return db, person, caregiver


def test_missing_coverage_is_unknown_not_no_events():
    db, person, _ = _db()
    try:
        end = datetime(2026, 10, 29, tzinfo=timezone.utc)
        result = assess_observation_coverage(db, person.id, "mobility", end - timedelta(days=14), end)
        assert result["coverage_status"] == "unknown"
        assert result["coverage_fraction"] is None
        assert "No complete observation-coverage record" in result["limitations"][0]
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)


def test_partial_coverage_does_not_claim_full_observation():
    db, person, caregiver = _db()
    try:
        end = datetime(2026, 10, 29, tzinfo=timezone.utc)
        start = end - timedelta(days=14)
        db.add(ObservationCoverage(
            id="covered-first-half", person_id=person.id, subject_type="mobility",
            window_start=start, window_end=start + timedelta(days=7), coverage_status="covered",
            source_evidence_ids=[], rationale="Caregiver explicitly observed mobility during this interval.",
            recorded_by_caregiver_id=caregiver.id,
        ))
        db.commit()
        result = assess_observation_coverage(db, person.id, "mobility", start, end)
        assert result["coverage_status"] == "partial"
        assert result["coverage_fraction"] == 0.5
        assert "event absence cannot be inferred" in result["limitations"][0]
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)


def test_adjacent_coverage_intervals_merge_without_double_counting():
    db, person, caregiver = _db()
    try:
        end = datetime(2026, 10, 29, tzinfo=timezone.utc)
        start = end - timedelta(days=14)
        for i, (left, right) in enumerate(((start, start + timedelta(days=7)), (start + timedelta(days=7), end))):
            db.add(ObservationCoverage(
                id=f"coverage-{i}", person_id=person.id, subject_type="mobility",
                window_start=left, window_end=right, coverage_status="covered",
                source_evidence_ids=[], rationale="Coverage explicitly recorded for this interval.",
                recorded_by_caregiver_id=caregiver.id,
            ))
        db.commit()
        result = assess_observation_coverage(db, person.id, "mobility", start, end)
        assert result["coverage_status"] == "covered"
        assert result["coverage_fraction"] == 1
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)
def test_coverage_records_are_isolated_by_person():
    db, person, caregiver = _db()
    try:
        other = Person(id="coverage-other-person", name="Other person")
        other_caregiver = Caregiver(id="coverage-other-caregiver", name="Other caregiver", relationship="family", person_id=other.id)
        db.add_all([other, other_caregiver])
        end = datetime(2026, 10, 29, tzinfo=timezone.utc)
        start = end - timedelta(days=14)
        db.add(ObservationCoverage(
            id="private-coverage", person_id=person.id, subject_type="mobility",
            window_start=start, window_end=end, coverage_status="covered",
            source_evidence_ids=[], rationale="Coverage for the first person only.",
            recorded_by_caregiver_id=caregiver.id,
        ))
        db.commit()
        result = assess_observation_coverage(db, other.id, "mobility", start, end)
        assert result["coverage_status"] == "unknown"
        assert result["records"] == []
        assert result["coverage_fraction"] is None
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)
