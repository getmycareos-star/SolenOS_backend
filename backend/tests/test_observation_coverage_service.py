from datetime import datetime, timedelta, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from pydantic import ValidationError
import pytest
from app.schemas.observation_coverage import ObservationCoverageCreate

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

def test_decrease_requires_complete_coverage_for_both_comparison_windows():
    from app.models.care import CareEvent
    from app.services.baseline import build_baseline
    from app.services.change_detection import detect_event_changes

    db, person, caregiver = _db()
    try:
        now = datetime.now(timezone.utc)
        for i, days_ago in enumerate((120, 100, 80, 60, 7)):
            db.add(CareEvent(
                id=f"fall-event-{i}", person_id=person.id, event_type="fall",
                status="recorded", occurred_at=now - timedelta(days=days_ago),
                title=f"Recorded fall {i}", created_by_caregiver_id=caregiver.id,
                evidence_ids=[f"source-{i}"],
            ))
        db.commit()
        baseline = build_baseline(db, person.id, "fall", None, now)
        assert baseline is not None
        db.add_all([
            ObservationCoverage(
                id="baseline-covered", person_id=person.id, subject_type="fall",
                window_start=baseline.window_start, window_end=baseline.window_end,
                coverage_status="covered", source_evidence_ids=[],
                rationale="The domain was explicitly observed throughout the baseline interval.",
                recorded_by_caregiver_id=caregiver.id,
            ),
            ObservationCoverage(
                id="current-covered", person_id=person.id, subject_type="fall",
                window_start=now - timedelta(days=42), window_end=now,
                coverage_status="covered", source_evidence_ids=[],
                rationale="The domain was explicitly observed throughout the current interval.",
                recorded_by_caregiver_id=caregiver.id,
            ),
        ])
        db.commit()
        changes = detect_event_changes(db, person.id, "fall", None, now)
        decreases = [row for row in changes if row.change_type.value == "decrease"]
        assert decreases, "A lower rate with complete coverage in both windows should be assessed"
        assert "Baseline observation coverage: covered" in decreases[0].explanation
        assert "current observation coverage: covered" in decreases[0].explanation
        assert decreases[0].baseline_coverage_status == "covered"
        assert decreases[0].current_coverage_status == "covered"
        assert decreases[0].baseline_coverage_fraction == pytest.approx(1.0)
        assert decreases[0].current_coverage_fraction == pytest.approx(1.0)
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)



def test_coverage_input_rejects_naive_timestamps():
    try:
        ObservationCoverageCreate(
            person_id="person",
            subject_type="mobility",
            window_start=datetime(2026, 10, 1),
            window_end=datetime(2026, 10, 2),
            coverage_status="covered",
            rationale="Coverage interval was explicitly observed.",
        )
    except ValidationError as exc:
        assert "explicit timezone offset" in str(exc)
    else:
        raise AssertionError("Naive timestamps must be rejected for coverage windows")



def test_coverage_rows_are_append_only():
    db, person, caregiver = _db()
    try:
        start = datetime(2026, 10, 1, tzinfo=timezone.utc)
        end = datetime(2026, 10, 2, tzinfo=timezone.utc)
        row = ObservationCoverage(
            id="immutable-coverage", person_id=person.id, subject_type="mobility",
            window_start=start, window_end=end, coverage_status="covered",
            source_evidence_ids=[], rationale="Original observation coverage record.",
            recorded_by_caregiver_id=caregiver.id,
        )
        db.add(row)
        db.commit()
        row.rationale = "Attempted rewrite of the original coverage record."
        with pytest.raises(ValueError, match="append-only"):
            db.commit()
        db.rollback()
        row = db.query(ObservationCoverage).filter_by(id="immutable-coverage").one()
        db.delete(row)
        with pytest.raises(ValueError, match="append-only"):
            db.commit()
        db.rollback()
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)


def test_conflicting_coverage_records_do_not_inflate_covered_fraction():
    db, person, caregiver = _db()
    try:
        end = datetime(2026, 10, 29, tzinfo=timezone.utc)
        start = end - timedelta(days=14)
        db.add_all([
            ObservationCoverage(
                id="coverage-claim-covered", person_id=person.id, subject_type="mobility",
                window_start=start, window_end=end, coverage_status="covered",
                source_evidence_ids=[], rationale="One record asserts full observation.",
                recorded_by_caregiver_id=caregiver.id,
            ),
            ObservationCoverage(
                id="coverage-claim-unknown", person_id=person.id, subject_type="mobility",
                window_start=start, window_end=end, coverage_status="unknown",
                source_evidence_ids=[], rationale="A second record cannot establish observation.",
                recorded_by_caregiver_id=caregiver.id,
            ),
        ])
        db.commit()
        result = assess_observation_coverage(db, person.id, "mobility", start, end)
        assert result["coverage_status"] == "unknown"
        assert result["covered_seconds"] == 0
        assert result["coverage_fraction"] == 0
        assert len(result["records"]) == 2
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)


def test_recurrence_pattern_persists_inter_event_interval_metrics():
    from app.models.care import CareEvent
    from app.services.pattern_detection import detect_patterns

    db, person, caregiver = _db()
    try:
        start = datetime(2026, 10, 1, tzinfo=timezone.utc)
        for i, days in enumerate((0, 2, 7, 8)):
            db.add(CareEvent(
                id=f"interval-event-{i}",
                person_id=person.id,
                event_type="mobility",
                status="recorded",
                occurred_at=start + timedelta(days=days),
                title=f"Recorded mobility event {i}",
                created_by_caregiver_id=caregiver.id,
                evidence_ids=[f"source-{i}"],
            ))
        db.commit()
        patterns = detect_patterns(db, person.id, "mobility", None, start + timedelta(days=10))
        recurrence = next(item for item in patterns if item.pattern_type.value == "recurrence")
        metrics = recurrence.pattern_metrics
        assert metrics["metric_version"] == "inter-event-intervals-v1"
        assert metrics["distinct_observation_times"] == 4
        assert metrics["inter_event_interval_count"] == 3
        assert metrics["inter_event_intervals_days"] == [2.0, 5.0, 1.0]
        assert metrics["minimum_interval_days"] == 1.0
        assert metrics["median_interval_days"] == 2.0
        assert metrics["maximum_interval_days"] == 5.0
        assert metrics["normalized_rate_available"] is False
        assert metrics["observation_coverage_status"] == "unknown"
        assert "no calibrated regularity threshold" in metrics["regularity_interpretation"]
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)
