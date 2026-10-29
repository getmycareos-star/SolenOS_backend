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
        # A qualitative "partial" declaration does not quantify what fraction
        # of the interval was observed; do not invent a numeric zero.
        assert result["coverage_fraction"] is None.5
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

        # A later contradictory/unknown coverage declaration must invalidate the
        # prior decrease claim rather than leave it active as if fully supported.
        db.add(ObservationCoverage(
            id="current-coverage-conflict", person_id=person.id, subject_type="fall",
            window_start=now - timedelta(days=3), window_end=now,
            coverage_status="unknown", source_evidence_ids=[],
            rationale="The final interval cannot be confirmed as continuously observed.",
            recorded_by_caregiver_id=caregiver.id,
        ))
        db.commit()
        reassessed = detect_event_changes(db, person.id, "fall", None, now)
        assert not any(row.change_type.value == "decrease" for row in reassessed)
        db.commit()
        persisted_old_decrease = db.query(Change).filter_by(id=decreases[0].id).one()
        assert persisted_old_decrease.is_active is False
        assert "no longer active" in persisted_old_decrease.explanation.lower()
        assert "historical assessment is retained" in persisted_old_decrease.explanation.lower()
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
                occurred_at_timezone="America/New_York",
                title=f"Recorded mobility event {i}",
                created_by_caregiver_id=caregiver.id,
                evidence_ids=[f"source-{i}"],
            ))
        # Complete coverage only between the first and last events is not
        # enough to calculate a rate over the full 90-day pattern window.
        db.add(ObservationCoverage(
            id="event-span-only-coverage",
            person_id=person.id,
            subject_type="mobility",
            window_start=start,
            window_end=start + timedelta(days=8),
            coverage_status="covered",
            source_evidence_ids=[],
            rationale="The event span was observed, but the surrounding 90-day window was not.",
            recorded_by_caregiver_id=caregiver.id,
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
        assert metrics["observation_coverage_status"] == "partial"
        assert metrics["local_time_distribution_status"] == "descriptive_only_no_causal_association"
        assert metrics["local_time_distribution"]["hour_of_day_counts"] == {"20": 4}
        assert "no calibrated regularity threshold" in metrics["regularity_interpretation"]
        # Expire ORM state and reload to prove the metrics were serialized to
        # the database, not merely attached to the in-memory result.
        from app.models.change import Pattern
        from app.schemas.change import Pattern as PatternSchema
        db.expire_all()
        persisted = db.query(Pattern).filter(Pattern.id == recurrence.id).one()
        assert persisted.pattern_metrics["inter_event_intervals_days"] == [2.0, 5.0, 1.0]
        api_pattern = PatternSchema.model_validate(persisted)
        assert api_pattern.pattern_metrics["observation_coverage_status"] == "partial"
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)


def test_coverage_schema_rejects_blank_domain():
    with pytest.raises(ValidationError):
        ObservationCoverageCreate(
            person_id="coverage-person",
            subject_type="   ",
            window_start=datetime(2026, 10, 1, tzinfo=timezone.utc),
            window_end=datetime(2026, 10, 2, tzinfo=timezone.utc),
            coverage_status="covered",
            rationale="Coverage interval was explicitly observed.",
        )


def test_coverage_schema_rejects_nonpositive_window():
    with pytest.raises(ValidationError, match="window_end must be later"):
        ObservationCoverageCreate(
            person_id="coverage-person",
            subject_type="mobility",
            window_start=datetime(2026, 10, 2, tzinfo=timezone.utc),
            window_end=datetime(2026, 10, 1, tzinfo=timezone.utc),
            coverage_status="covered",
            rationale="Invalid interval.",
        )


def test_lower_record_count_without_coverage_does_not_claim_decrease():
    from app.models.care import CareEvent
    from app.services.change_detection import detect_event_changes

    db, person, caregiver = _db()
    try:
        now = datetime.now(timezone.utc)
        for i, days_ago in enumerate((120, 100, 80, 60, 7)):
            db.add(CareEvent(
                id=f"uncovered-fall-{i}",
                person_id=person.id,
                event_type="fall",
                status="recorded",
                occurred_at=now - timedelta(days=days_ago),
                title=f"Recorded fall {i}",
                created_by_caregiver_id=caregiver.id,
                evidence_ids=[f"uncovered-source-{i}"],
            ))
        db.commit()
        changes = detect_event_changes(db, person.id, "fall", None, now)
        assert not [row for row in changes if row.change_type.value == "decrease"], (
            "A lower recorded count is not a defensible decrease without complete "
            "observation coverage for both comparison windows."
        )
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)


def test_pattern_reassessment_supersedes_metrics_when_coverage_changes():
    from app.models.care import CareEvent
    from app.models.change import Pattern
    from app.services.pattern_detection import detect_patterns

    db, person, caregiver = _db()
    try:
        event_start = datetime(2026, 10, 1, tzinfo=timezone.utc)
        reference_time = event_start + timedelta(days=10)
        for i, days in enumerate((0, 2, 7, 8)):
            db.add(CareEvent(
                id=f"coverage-recalc-event-{i}",
                person_id=person.id,
                event_type="mobility",
                status="recorded",
                occurred_at=event_start + timedelta(days=days),
                occurred_at_timezone="America/New_York",
                title=f"Recorded mobility event {i}",
                created_by_caregiver_id=caregiver.id,
                evidence_ids=[f"coverage-recalc-source-{i}"],
            ))
        db.commit()

        first = next(
            item for item in detect_patterns(db, person.id, "mobility", None, reference_time)
            if item.pattern_type.value == "recurrence"
        )
        assert first.frequency is None
        first_id = first.id

        db.add(ObservationCoverage(
            id="full-90-day-coverage",
            person_id=person.id,
            subject_type="mobility",
            window_start=reference_time - timedelta(days=90),
            window_end=reference_time,
            coverage_status="covered",
            source_evidence_ids=[],
            rationale="The mobility domain was explicitly observed for the full analysis window.",
            recorded_by_caregiver_id=caregiver.id,
        ))
        db.commit()

        second = next(
            item for item in detect_patterns(db, person.id, "mobility", None, reference_time)
            if item.pattern_type.value == "recurrence"
        )
        db.refresh(second)
        old = db.query(Pattern).filter(Pattern.id == first_id).one()
        assert second.id != first_id
        assert second.frequency == pytest.approx(4 / 90)
        assert second.rate_per_period == pytest.approx(4 / 3)
        assert second.pattern_metrics["observation_coverage_status"] == "covered"
        assert old.is_active is False
        assert old.superseded_by_pattern_id == second.id
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)


def test_conflicting_coverage_declaration_reduces_covered_fraction():
    db, person, caregiver = _db()
    try:
        end = datetime(2026, 10, 29, tzinfo=timezone.utc)
        start = end - timedelta(days=14)
        db.add_all([
            ObservationCoverage(
                id="full-covered", person_id=person.id, subject_type="mobility",
                window_start=start, window_end=end, coverage_status="covered",
                source_evidence_ids=[], rationale="Coverage declared for the full interval.",
                recorded_by_caregiver_id=caregiver.id,
            ),
            ObservationCoverage(
                id="conflicting-partial", person_id=person.id, subject_type="mobility",
                window_start=start + timedelta(days=5), window_end=start + timedelta(days=9),
                coverage_status="partial", source_evidence_ids=[],
                rationale="A conflicting source says observation was partial.",
                recorded_by_caregiver_id=caregiver.id,
            ),
        ])
        db.commit()
        result = assess_observation_coverage(db, person.id, "mobility", start, end)
        assert result["coverage_status"] == "partial"
        assert result["coverage_fraction"] < 1.0
        assert result["coverage_fraction"] == pytest.approx(10 / 14)
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)


def test_rate_decrease_is_not_created_without_explicit_coverage():
    from app.models.care import CareEvent
    from app.services.change_detection import detect_event_changes

    db, person, caregiver = _db()
    try:
        now = datetime.now(timezone.utc)
        # Several older events and only one current event create a lower recorded
        # rate, but without coverage records that is not evidence of a true decrease.
        for i, days_ago in enumerate((120, 100, 80, 60, 7)):
            db.add(CareEvent(
                id=f"fall-no-coverage-{i}", person_id=person.id, event_type="fall",
                status="recorded", occurred_at=now - timedelta(days=days_ago),
                title=f"Recorded fall {i}", created_by_caregiver_id=caregiver.id,
                evidence_ids=[f"source-no-coverage-{i}"],
            ))
        db.commit()
        changes = detect_event_changes(db, person.id, "fall", None, now)
        assert not any(row.change_type.value == "decrease" for row in changes)
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)


def test_frequency_pattern_requires_covered_equal_windows_and_explains_measurements():
    from app.models.care import CareEvent
    from app.core.change_enums import PatternType
    from app.services.pattern_detection import detect_patterns

    db, person, caregiver = _db()
    try:
        now = datetime.now(timezone.utc)
        prior_start = now - timedelta(days=60)
        current_start = now - timedelta(days=30)
        # One event in the prior 30-day interval and three in the current interval.
        for i, days_ago in enumerate((45, 20, 10, 5)):
            db.add(CareEvent(
                id=f"frequency-event-{i}", person_id=person.id, event_type="mobility",
                status="recorded", occurred_at=now - timedelta(days=days_ago),
                title=f"Recorded mobility event {i}", created_by_caregiver_id=caregiver.id,
                evidence_ids=[f"frequency-source-{i}"],
            ))
        db.add_all([
            ObservationCoverage(
                id="frequency-prior-coverage", person_id=person.id, subject_type="mobility",
                window_start=prior_start, window_end=current_start, coverage_status="covered",
                source_evidence_ids=[], rationale="Prior interval explicitly observed throughout.",
                recorded_by_caregiver_id=caregiver.id,
            ),
            ObservationCoverage(
                id="frequency-current-coverage", person_id=person.id, subject_type="mobility",
                window_start=current_start, window_end=now, coverage_status="covered",
                source_evidence_ids=[], rationale="Current interval explicitly observed throughout.",
                recorded_by_caregiver_id=caregiver.id,
            ),
        ])
        db.commit()
        patterns = detect_patterns(db, person.id, "mobility", None, now)
        frequency = [p for p in patterns if p.pattern_type == PatternType.FREQUENCY_INCREASE]
        assert len(frequency) == 1
        assert frequency[0].pattern_metrics["prior_event_count"] == 1
        assert frequency[0].pattern_metrics["current_event_count"] == 3
        assert frequency[0].pattern_metrics["absolute_rate_delta_per_30_days"] == 2.0
        assert frequency[0].pattern_metrics["prior_coverage_status"] == "covered"
        assert frequency[0].pattern_metrics["current_coverage_status"] == "covered"
        assert frequency[0].confidence is None
        assert "does not establish cause" in frequency[0].alternative_interpretations[0]
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)


def test_frequency_pattern_is_withheld_when_coverage_is_unknown():
    from app.models.care import CareEvent
    from app.core.change_enums import PatternType
    from app.services.pattern_detection import detect_patterns

    db, person, caregiver = _db()
    try:
        now = datetime.now(timezone.utc)
        for i, days_ago in enumerate((45, 20, 10, 5)):
            db.add(CareEvent(
                id=f"frequency-unknown-event-{i}", person_id=person.id, event_type="mobility",
                status="recorded", occurred_at=now - timedelta(days=days_ago),
                title=f"Recorded mobility event {i}", created_by_caregiver_id=caregiver.id,
                evidence_ids=[f"frequency-unknown-source-{i}"],
            ))
        db.commit()
        patterns = detect_patterns(db, person.id, "mobility", None, now)
        assert not any(
            p.pattern_type in {PatternType.FREQUENCY_INCREASE, PatternType.FREQUENCY_DECREASE}
            for p in patterns
        )
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)


def test_change_assessment_is_rebuilt_when_coverage_provenance_changes(monkeypatch):
    from app.core.change_enums import ChangeDirection, ChangeType
    from app.models.care import CareEvent
    from app.models.change import Change
    from app.services import significance
    from app.services.change_detection import _create_change

    db, person, caregiver = _db()
    try:
        now = datetime(2026, 10, 29, tzinfo=timezone.utc)
        event = CareEvent(
            id="coverage-refresh-event",
            person_id=person.id,
            event_type="mobility",
            status="recorded",
            occurred_at=now - timedelta(days=2),
            title="Observed mobility event",
            created_by_caregiver_id=caregiver.id,
            evidence_ids=["coverage-refresh-source"],
        )
        db.add(event)
        db.commit()
        monkeypatch.setattr(significance, "assess_change", lambda *args, **kwargs: None)

        base_comparison = {
            "relative_change_percent": None,
            "current_window_start": now - timedelta(days=42),
            "current_window_end": now,
            "baseline_coverage_status": "unknown",
            "current_coverage_status": "unknown",
            "baseline_coverage_fraction": None,
            "current_coverage_fraction": None,
        }
        first = _create_change(
            db=db, person_id=person.id, subject_type="mobility", subject_id="",
            change_type=ChangeType.EMERGENCE, change_direction=ChangeDirection.INCREASING,
            previous_state="No comparable prior events were recorded in the baseline window",
            current_state="1 event recorded in the current observation window",
            previous_count=0, current_count=1, baseline_id=None, baseline=None,
            comparison=base_comparison, events=[event], now=now,
        )
        db.commit()
        assert first.is_active is True
        assert first.current_coverage_status == "unknown"

        updated_comparison = {
            **base_comparison,
            "baseline_coverage_status": "covered",
            "current_coverage_status": "covered",
            "baseline_coverage_fraction": 1.0,
            "current_coverage_fraction": 1.0,
        }
        second = _create_change(
            db=db, person_id=person.id, subject_type="mobility", subject_id="",
            change_type=ChangeType.EMERGENCE, change_direction=ChangeDirection.INCREASING,
            previous_state="No comparable prior events were recorded in the baseline window",
            current_state="1 event recorded in the current observation window",
            previous_count=0, current_count=1, baseline_id=None, baseline=None,
            comparison=updated_comparison, events=[event], now=now + timedelta(minutes=1),
        )
        db.commit()
        rows = db.query(Change).filter(
            Change.person_id == person.id,
            Change.subject_type == "mobility",
            Change.change_type == ChangeType.EMERGENCE,
        ).order_by(Change.created_at.asc()).all()
        assert len(rows) == 2
        assert rows[0].is_active is False
        assert rows[0].superseded_by_change_id == second.id
        assert rows[1].is_active is True
        assert rows[1].baseline_coverage_status == "covered"
        assert rows[1].current_coverage_status == "covered"
        assert rows[1].baseline_coverage_fraction == pytest.approx(1.0)
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)


def test_reassessment_retires_patterns_when_all_source_events_are_invalidated():
    from app.core.change_enums import PatternType
    from app.models.care import CareEvent
    from app.models.change import Pattern
    from app.services.pattern_detection import detect_patterns

    db, person, caregiver = _db()
    try:
        start = datetime(2026, 10, 1, tzinfo=timezone.utc)
        reference_time = start + timedelta(days=10)
        events = []
        for index, day in enumerate((0, 2, 7, 8)):
            event = CareEvent(
                id=f"retire-pattern-event-{index}",
                person_id=person.id,
                event_type="mobility",
                status="recorded",
                occurred_at=start + timedelta(days=day),
                title=f"Mobility occurrence {index}",
                created_by_caregiver_id=caregiver.id,
                evidence_ids=[f"retire-pattern-source-{index}"],
            )
            events.append(event)
            db.add(event)
        db.commit()

        first = detect_patterns(db, person.id, "mobility", None, reference_time)
        db.commit()
        recurrence = next(item for item in first if item.pattern_type == PatternType.RECURRENCE)
        assert recurrence.is_active is True

        for event in events:
            event.status = "invalidated"
        db.commit()

        second = detect_patterns(db, person.id, "mobility", None, reference_time)
        db.commit()
        assert not second
        persisted = db.query(Pattern).filter(
            Pattern.person_id == person.id,
            Pattern.subject_type == "mobility",
        ).all()
        assert persisted
        assert all(item.is_active is False for item in persisted)
        assert any(
            "retained as historical context" in " ".join(item.alternative_interpretations or [])
            for item in persisted
        )
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)


def test_partial_uncovered_interval_does_not_label_whole_window_uncovered():
    db, person, caregiver = _db()
    try:
        end = datetime(2026, 10, 29, tzinfo=timezone.utc)
        start = end - timedelta(days=14)
        db.add(ObservationCoverage(
            id="only-first-week-uncovered", person_id=person.id, subject_type="mobility",
            window_start=start, window_end=start + timedelta(days=7), coverage_status="uncovered",
            source_evidence_ids=[], rationale="Caregiver confirms the first week was not observed.",
            recorded_by_caregiver_id=caregiver.id,
        ))
        db.commit()
        result = assess_observation_coverage(db, person.id, "mobility", start, end)
        assert result["coverage_status"] == "unknown"
        assert result["coverage_fraction"] == 0
        assert "do not establish observation status" in result["limitations"][0]
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)


def test_entire_window_must_be_explicitly_uncovered_for_uncovered_status():
    db, person, caregiver = _db()
    try:
        end = datetime(2026, 10, 29, tzinfo=timezone.utc)
        start = end - timedelta(days=14)
        db.add(ObservationCoverage(
            id="whole-window-uncovered", person_id=person.id, subject_type="mobility",
            window_start=start, window_end=end, coverage_status="uncovered",
            source_evidence_ids=[], rationale="Caregiver confirms the whole interval was not observed.",
            recorded_by_caregiver_id=caregiver.id,
        ))
        db.commit()
        result = assess_observation_coverage(db, person.id, "mobility", start, end)
        assert result["coverage_status"] == "uncovered"
        assert result["coverage_fraction"] == 0
        assert "entire requested interval" in result["limitations"][0]
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)


def test_explicit_partial_coverage_is_not_reported_as_unknown():
    db, person, caregiver = _db()
    try:
        end = datetime(2026, 10, 29, tzinfo=timezone.utc)
        start = end - timedelta(days=14)
        db.add(ObservationCoverage(
            id="partial-only", person_id=person.id, subject_type="mobility",
            window_start=start, window_end=end, coverage_status="partial",
            source_evidence_ids=[], rationale="Caregiver observed only part of the interval.",
            recorded_by_caregiver_id=caregiver.id,
        ))
        db.commit()
        result = assess_observation_coverage(db, person.id, "mobility", start, end)
        assert result["coverage_status"] == "partial"
        assert result["coverage_fraction"] == 0
        assert "explicitly partial" in result["limitations"][0]
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)
