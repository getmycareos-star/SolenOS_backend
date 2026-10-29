"""Deterministic, database-backed benchmark for longitudinal change safeguards.

The benchmark score is computed from observed service outcomes against labelled
scenario expectations. These are synthetic test fixtures, not care data and not
production examples.
"""
from datetime import datetime, timedelta, timezone
import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models.care import Person, Caregiver, CareEvent
from app.models.change import Change
from app.core.change_enums import ChangeType, ChangeDirection
from app.models.observation_coverage import ObservationCoverage
from app.services.baseline import get_longitudinal_event_counts
from app.services.change_detection import detect_event_changes
from app.services.pattern_detection import detect_patterns


engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def _record_event(db, person, caregiver, event_id, days_ago, evidence_ids=None):
    db.add(CareEvent(
        id=event_id,
        person_id=person.id,
        event_type="fall",
        status="recorded",
        occurred_at=REFERENCE_TIME - timedelta(days=days_ago),
        title=f"Fixture event {event_id}",
        evidence_ids=evidence_ids or [],
        created_by_caregiver_id=caregiver.id,
    ))


REFERENCE_TIME = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)


def _case_identity(db, suffix):
    person = Person(id=f"benchmark-person-{suffix}", name="Benchmark fixture")
    caregiver = Caregiver(
        id=f"benchmark-caregiver-{suffix}",
        name="Benchmark fixture caregiver",
        relationship="family",
        person_id=person.id,
    )
    db.add_all([person, caregiver])
    db.flush()
    return person, caregiver


def _add_full_coverage(db, person, caregiver, baseline_start):
    baseline_end = REFERENCE_TIME - timedelta(days=42)
    db.add_all([
        ObservationCoverage(
            id=f"coverage-baseline-{person.id}",
            person_id=person.id,
            subject_type="fall",
            window_start=baseline_start,
            window_end=baseline_end,
            coverage_status="covered",
            source_evidence_ids=[],
            rationale="Benchmark fixture declares full observation for this interval.",
            recorded_by_caregiver_id=caregiver.id,
        ),
        ObservationCoverage(
            id=f"coverage-current-{person.id}",
            person_id=person.id,
            subject_type="fall",
            window_start=baseline_end,
            window_end=REFERENCE_TIME,
            coverage_status="covered",
            source_evidence_ids=[],
            rationale="Benchmark fixture declares full observation for this interval.",
            recorded_by_caregiver_id=caregiver.id,
        ),
    ])


def _add_trend_assessments(db, person, event_ids):
    """Add three distinct, source-linked rising assessments for benchmark tests."""
    windows = ((80, 70), (60, 50), (40, 30))
    for index, (start_days, end_days) in enumerate(windows):
        db.add(Change(
            id=f"trend-change-{person.id}-{index}",
            person_id=person.id,
            subject_type="fall",
            subject_id="",
            change_type=ChangeType.INCREASE,
            change_direction=ChangeDirection.INCREASING,
            previous_state=f"{index + 1} recorded events in prior comparison",
            current_state=f"{index + 2} recorded events in current comparison",
            magnitude=float(index + 1),
            previous_count=index + 1,
            current_count=index + 2,
            comparison_window_start=REFERENCE_TIME - timedelta(days=start_days),
            comparison_window_end=REFERENCE_TIME - timedelta(days=end_days),
            baseline_coverage_status="covered",
            current_coverage_status="covered",
            evidence_ids=[event_ids[index]],
            confirmation_status="detected",
            is_active=True,
        ))


def test_longitudinal_trend_requires_distinct_windows_source_events_and_full_coverage():
    Base.metadata.create_all(bind=engine)
    db = Session()
    try:
        person, caregiver = _case_identity(db, "trend-covered")
        event_ids = []
        for event_id, days in (("trend-event-1", 70), ("trend-event-2", 50), ("trend-event-3", 30)):
            _record_event(db, person, caregiver, event_id, days)
            event_ids.append(event_id)
        db.add(ObservationCoverage(
            id=f"trend-coverage-{person.id}",
            person_id=person.id,
            subject_type="fall",
            window_start=REFERENCE_TIME - timedelta(days=90),
            window_end=REFERENCE_TIME,
            coverage_status="covered",
            source_evidence_ids=[],
            rationale="Benchmark fixture explicitly covers the complete trend interval.",
            recorded_by_caregiver_id=caregiver.id,
        ))
        _add_trend_assessments(db, person, event_ids)
        db.flush()
        patterns = detect_patterns(db, person.id, "fall", None, REFERENCE_TIME)
        trend = next((pattern for pattern in patterns if pattern.pattern_type.value == "trend"), None)
        assert trend is not None
        assert set(trend.event_ids) == set(event_ids)
        assert len(set(trend.change_ids or [])) == 3
        assert trend.direction.value == "increasing"
        assert trend.confidence is None

        person, caregiver = _case_identity(db, "trend-unknown-coverage")
        event_ids = []
        for event_id, days in (("trend-unknown-event-1", 70), ("trend-unknown-event-2", 50), ("trend-unknown-event-3", 30)):
            _record_event(db, person, caregiver, event_id, days)
            event_ids.append(event_id)
        _add_trend_assessments(db, person, event_ids)
        db.flush()
        patterns = detect_patterns(db, person.id, "fall", None, REFERENCE_TIME)
        assert all(pattern.pattern_type.value != "trend" for pattern in patterns)
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)


def test_longitudinal_change_benchmark_reports_measured_case_score():
    Base.metadata.create_all(bind=engine)
    db = Session()
    try:
        outcomes = []

        # Case 1: a genuinely higher normalized rate with complete coverage.
        person, caregiver = _case_identity(db, "increase")
        for event_id, days in (("inc-base-1", 150), ("inc-base-2", 100),
                               ("inc-now-1", 35), ("inc-now-2", 25),
                               ("inc-now-3", 15), ("inc-now-4", 5)):
            _record_event(db, person, caregiver, event_id, days)
        _add_full_coverage(db, person, caregiver, REFERENCE_TIME - timedelta(days=150))
        db.flush()
        changes = detect_event_changes(db, person.id, "fall", None, REFERENCE_TIME)
        db.flush()
        observed = any(change.change_type.value == "increase" for change in changes)
        outcomes.append({"case": "covered_increase", "expected": True, "observed": observed, "pass": observed is True})

        # Case 2: a lower rate with complete coverage.
        person, caregiver = _case_identity(db, "decrease")
        for event_id, days in (("dec-base-1", 120), ("dec-base-2", 100),
                               ("dec-base-3", 80), ("dec-base-4", 60),
                               ("dec-now-1", 7)):
            _record_event(db, person, caregiver, event_id, days)
        _add_full_coverage(db, person, caregiver, REFERENCE_TIME - timedelta(days=120))
        db.flush()
        changes = detect_event_changes(db, person.id, "fall", None, REFERENCE_TIME)
        observed = any(change.change_type.value == "decrease" for change in changes)
        outcomes.append({"case": "covered_decrease", "expected": True, "observed": observed, "pass": observed is True})

        # Case 3: fewer records without coverage must not be called a decrease.
        person, caregiver = _case_identity(db, "uncovered-decrease")
        for event_id, days in (("unc-base-1", 120), ("unc-base-2", 100),
                               ("unc-base-3", 80), ("unc-base-4", 60),
                               ("unc-now-1", 7)):
            _record_event(db, person, caregiver, event_id, days)
        db.flush()
        changes = detect_event_changes(db, person.id, "fall", None, REFERENCE_TIME)
        observed = any(change.change_type.value == "decrease" for change in changes)
        outcomes.append({"case": "uncovered_apparent_decrease", "expected": False, "observed": observed, "pass": observed is False})

        # Case 4: recurrence can be described from recorded events, but its rate
        # must be withheld when the interval is not explicitly fully observed.
        person, caregiver = _case_identity(db, "recurrence")
        for event_id, days in (("rec-1", 28), ("rec-2", 14), ("rec-3", 2)):
            _record_event(db, person, caregiver, event_id, days)
        db.flush()
        patterns = detect_patterns(db, person.id, "fall", None, REFERENCE_TIME)
        recurrence = next((p for p in patterns if p.pattern_type.value == "recurrence"), None)
        observed = bool(
            recurrence
            and recurrence.pattern_metrics
            and recurrence.pattern_metrics.get("normalized_rate_available") is False
            and recurrence.confidence is None
        )
        outcomes.append({"case": "recurrence_rate_withheld_without_coverage", "expected": True, "observed": observed, "pass": observed is True})

        # Case 5: exact evidence-copy duplicates must not inflate occurrence counts.
        person, caregiver = _case_identity(db, "duplicate")
        _record_event(db, person, caregiver, "dup-original", 7, ["shared-source-1"])
        _record_event(db, person, caregiver, "dup-copy", 7, ["shared-source-1"])
        db.flush()
        counts = get_longitudinal_event_counts(db, person.id, "fall", None, REFERENCE_TIME)
        observed = counts["current_window_count"] == 1
        outcomes.append({"case": "duplicate_source_not_double_counted", "expected": 1, "observed": counts["current_window_count"], "pass": observed})

        # Case 6: same source file can document separate dated occurrences.
        # Reusing the source ID must not erase a genuinely distinct event.
        person, caregiver = _case_identity(db, "shared-source-distinct-occurrences")
        _record_event(db, person, caregiver, "shared-src-event-1", 20, ["care-log-1"])
        _record_event(db, person, caregiver, "shared-src-event-2", 5, ["care-log-1"])
        db.flush()
        counts = get_longitudinal_event_counts(db, person.id, "fall", None, REFERENCE_TIME)
        observed = counts["current_window_count"] == 2
        outcomes.append({
            "case": "same_source_distinct_dates_remain_distinct_occurrences",
            "expected": 2, "observed": counts["current_window_count"], "pass": observed,
        })

        # Case 7: an event with unknown historical coverage must remain visible,
        # but the stored assessment must explicitly state that prior absence is
        # not established. The caregiver-facing label should say "first observed".
        person, caregiver = _case_identity(db, "uncovered-emergence")
        _record_event(db, person, caregiver, "uncov-emergence-1", 5)
        db.flush()
        changes = detect_event_changes(db, person.id, "fall", None, REFERENCE_TIME)
        first_observed = next((change for change in changes if change.change_type.value == "emergence"), None)
        observed = bool(
            first_observed
            and first_observed.baseline_coverage_status == "unknown"
            and "first observed in the available records" in (first_observed.explanation or "").lower()
            and "does not establish" in (first_observed.explanation or "").lower()
            and "absence cannot be inferred" in (first_observed.previous_state or "").lower()
        )
        outcomes.append({
            "case": "unknown_coverage_first_observed_is_explicitly_qualified",
            "expected": True, "observed": observed, "pass": observed is True,
        })

        # Case 8: the same zero-event baseline supports emergence only when the
        # entire baseline interval was explicitly observed.
        person, caregiver = _case_identity(db, "covered-emergence")
        _record_event(db, person, caregiver, "cov-emergence-1", 5)
        _add_full_coverage(db, person, caregiver, REFERENCE_TIME - timedelta(days=180))
        db.flush()
        changes = detect_event_changes(db, person.id, "fall", None, REFERENCE_TIME)
        observed = any(change.change_type.value == "emergence" for change in changes)
        outcomes.append({
            "case": "covered_zero_event_baseline_supports_emergence",
            "expected": True, "observed": observed, "pass": observed is True,
        })

        # Case 9: messy real-world comparison — three falls recorded over six
        # weeks, with no falls in older records but no proof the earlier interval
        # was fully observed. Report recurrence and the actual dates/count, while
        # withholding a normalized rate and not claiming the falls began recently.
        person, caregiver = _case_identity(db, "three-falls-six-weeks")
        for event_id, days in (("falls-six-weeks-1", 35), ("falls-six-weeks-2", 19), ("falls-six-weeks-3", 4)):
            _record_event(db, person, caregiver, event_id, days)
        db.flush()
        changes = detect_event_changes(db, person.id, "fall", None, REFERENCE_TIME)
        patterns = detect_patterns(db, person.id, "fall", None, REFERENCE_TIME)
        first_observed = next((change for change in changes if change.change_type.value == "emergence"), None)
        recurrence = next((pattern for pattern in patterns if pattern.pattern_type.value == "recurrence"), None)
        observed = bool(
            first_observed
            and first_observed.baseline_coverage_status == "unknown"
            and "absence cannot be inferred" in (first_observed.previous_state or "").lower()
            and recurrence
            and recurrence.evidence_count == 3
            and recurrence.confidence is None
            and recurrence.pattern_metrics
            and recurrence.pattern_metrics.get("normalized_rate_available") is False
            and len(recurrence.event_ids) == 3
        )
        outcomes.append({
            "case": "three_falls_six_weeks_without_claiming_eight_month_absence",
            "expected": True, "observed": observed, "pass": observed is True,
        })

        # Case 10: overlapping contradictory coverage declarations must not
        # be upgraded to complete coverage by unioning their intervals.
        person, caregiver = _case_identity(db, "conflicting-coverage")
        conflict_start = REFERENCE_TIME - timedelta(days=30)
        db.add_all([
            ObservationCoverage(
                id=f"conflict-covered-{person.id}", person_id=person.id, subject_type="fall",
                window_start=conflict_start, window_end=REFERENCE_TIME,
                coverage_status="covered", source_evidence_ids=[],
                rationale="One source declares full observation.", recorded_by_caregiver_id=caregiver.id,
            ),
            ObservationCoverage(
                id=f"conflict-unknown-{person.id}", person_id=person.id, subject_type="fall",
                window_start=REFERENCE_TIME - timedelta(days=3), window_end=REFERENCE_TIME,
                coverage_status="unknown", source_evidence_ids=[],
                rationale="Another source cannot establish observation status.", recorded_by_caregiver_id=caregiver.id,
            ),
        ])
        db.flush()
        coverage = __import__("app.services.observation_coverage", fromlist=["assess_observation_coverage"]).assess_observation_coverage(
            db, person.id, "fall", conflict_start, REFERENCE_TIME
        )
        observed = coverage["coverage_status"] != "covered" and coverage["coverage_fraction"] != 1.0
        outcomes.append({
            "case": "conflicting_coverage_cannot_be_promoted_to_complete",
            "expected": True, "observed": observed, "pass": observed is True,
        })

        # Case 11: future-dated records are excluded from a past reference-time
        # analysis, even if they are already present in storage.
        person, caregiver = _case_identity(db, "future-event")
        _record_event(db, person, caregiver, "past-event-1", 5)
        db.add(CareEvent(
            id="future-event-1", person_id=person.id, event_type="fall", status="recorded",
            occurred_at=REFERENCE_TIME + timedelta(days=1), title="Future fixture event",
            evidence_ids=[], created_by_caregiver_id=caregiver.id,
        ))
        db.flush()
        counts = get_longitudinal_event_counts(db, person.id, "fall", None, REFERENCE_TIME)
        observed = counts["current_window_count"] == 1 and all(
            (
                event.occurred_at.replace(tzinfo=timezone.utc)
                if event.occurred_at.tzinfo is None
                else event.occurred_at.astimezone(timezone.utc)
            ) <= REFERENCE_TIME
            for event in counts["recent_events"]
        )
        outcomes.append({
            "case": "future_dated_event_excluded_from_reference_time",
            "expected": 1, "observed": counts["current_window_count"], "pass": observed,
        })

        # Case 12: a covered baseline with no events may support a zero count;
        # without that coverage, the same count remains unknown (case 7).
        person, caregiver = _case_identity(db, "covered-emergence-zero-count")
        _record_event(db, person, caregiver, "covered-zero-count-current", 5)
        _add_full_coverage(db, person, caregiver, REFERENCE_TIME - timedelta(days=180))
        db.flush()
        changes = detect_event_changes(db, person.id, "fall", None, REFERENCE_TIME)
        emergence = next((change for change in changes if change.change_type.value == "emergence"), None)
        observed = bool(
            emergence
            and emergence.baseline_coverage_status == "covered"
            and emergence.previous_count == 0
        )
        outcomes.append({
            "case": "covered_baseline_can_support_explicit_zero_prior_count",
            "expected": True, "observed": observed, "pass": observed is True,
        })

        score = sum(1 for outcome in outcomes if outcome["pass"]) / len(outcomes)
        report = {
            "benchmark": "longitudinal-change-safeguards-v3",
            "reference_time": REFERENCE_TIME.isoformat(),
            "cases_passed": sum(1 for outcome in outcomes if outcome["pass"]),
            "cases_total": len(outcomes),
            "score": score,
            "outcomes": outcomes,
        }
        print("LONGITUDINAL_CHANGE_BENCHMARK=" + json.dumps(report, sort_keys=True))
        assert score == 1.0, json.dumps(report, indent=2)
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)



def test_recurrence_uses_full_included_event_span_for_coverage_denominator():
    """Older events remain visible, but rates require coverage across the whole span."""
    Base.metadata.create_all(bind=engine)
    db = Session()
    try:
        # A 90-day source query previously discarded the two older occurrences,
        # making the longitudinal pattern incomplete without saying so.
        person, caregiver = _case_identity(db, "year-long-recurrence-covered")
        for event_id, days in (
            ("year-recurrence-150d", 150),
            ("year-recurrence-100d", 100),
            ("year-recurrence-30d", 30),
        ):
            _record_event(db, person, caregiver, event_id, days)
        _add_full_coverage(db, person, caregiver, REFERENCE_TIME - timedelta(days=180))
        db.flush()

        patterns = detect_patterns(db, person.id, "fall", None, REFERENCE_TIME)
        recurrence = next((p for p in patterns if p.pattern_type.value == "recurrence"), None)
        assert recurrence is not None
        assert set(recurrence.event_ids) == {
            "year-recurrence-150d",
            "year-recurrence-100d",
            "year-recurrence-30d",
        }
        assert recurrence.pattern_metrics["distinct_event_records"] == 3
        assert recurrence.pattern_metrics["analysis_window_days"] == 150.0
        assert recurrence.pattern_metrics["normalized_rate_available"] is True
        assert recurrence.rate_per_period is not None

        # The same old events with only recent coverage still support a
        # descriptive recurrence, but cannot support a rate over 150 days.
        person, caregiver = _case_identity(db, "year-long-recurrence-partial-coverage")
        for event_id, days in (
            ("year-partial-150d", 150),
            ("year-partial-100d", 100),
            ("year-partial-30d", 30),
        ):
            _record_event(db, person, caregiver, event_id, days)
        db.add(ObservationCoverage(
            id=f"year-partial-current-{person.id}",
            person_id=person.id,
            subject_type="fall",
            window_start=REFERENCE_TIME - timedelta(days=90),
            window_end=REFERENCE_TIME,
            coverage_status="covered",
            source_evidence_ids=[],
            rationale="Only the most recent 90 days were explicitly observed.",
            recorded_by_caregiver_id=caregiver.id,
        ))
        db.flush()
        patterns = detect_patterns(db, person.id, "fall", None, REFERENCE_TIME)
        recurrence = next((p for p in patterns if p.pattern_type.value == "recurrence"), None)
        assert recurrence is not None
        assert set(recurrence.event_ids) == {
            "year-partial-150d",
            "year-partial-100d",
            "year-partial-30d",
        }
        assert recurrence.pattern_metrics["analysis_window_days"] == 150.0
        assert recurrence.pattern_metrics["normalized_rate_available"] is False
        assert recurrence.rate_per_period is None
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)


def test_pattern_reassessment_is_idempotent_and_deduplicates_same_source_occurrence():
    """Repeated reconciliation must not multiply patterns or copied source rows."""
    Base.metadata.create_all(bind=engine)
    db = Session()
    try:
        person, caregiver = _case_identity(db, "idempotent-pattern-reassessment")
        # Three distinct source-backed occurrences are needed for a cluster.
        for event_id, days, source_ids in (
            ("idem-original-1", 6, ["shared-source-a"]),
            ("idem-copy-1", 6, ["shared-source-a"]),
            ("idem-event-2", 4, ["shared-source-b"]),
            ("idem-event-3", 2, ["shared-source-c"]),
        ):
            _record_event(db, person, caregiver, event_id, days, source_ids)
        db.flush()

        first = detect_patterns(db, person.id, "fall", None, REFERENCE_TIME)
        db.flush()
        first_cluster = next(
            (pattern for pattern in first if pattern.pattern_type.value == "clustering"),
            None,
        )
        assert first_cluster is not None
        assert set(first_cluster.event_ids) == {
            "idem-original-1", "idem-event-2", "idem-event-3"
        }

        first_id = first_cluster.id
        second = detect_patterns(db, person.id, "fall", None, REFERENCE_TIME)
        db.flush()
        second_cluster = next(
            (pattern for pattern in second if pattern.pattern_type.value == "clustering"),
            None,
        )
        assert second_cluster is not None
        assert second_cluster.id == first_id

        active_clusters = (
            db.query(Pattern)
            .filter(
                Pattern.person_id == person.id,
                Pattern.subject_type == "fall",
                Pattern.pattern_type == second_cluster.pattern_type,
                Pattern.is_active.is_(True),
            )
            .all()
        )
        assert len(active_clusters) == 1
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)
