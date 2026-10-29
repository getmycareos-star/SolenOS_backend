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

        # Case 7: one recent event with no explicit historical observation
        # coverage is first observed, but must not be called an emergence.
        person, caregiver = _case_identity(db, "uncovered-emergence")
        _record_event(db, person, caregiver, "uncov-emergence-1", 5)
        db.flush()
        changes = detect_event_changes(db, person.id, "fall", None, REFERENCE_TIME)
        observed = any(change.change_type.value == "emergence" for change in changes)
        outcomes.append({
            "case": "unknown_baseline_coverage_blocks_emergence_claim",
            "expected": False, "observed": observed, "pass": observed is False,
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

        score = sum(1 for outcome in outcomes if outcome["pass"]) / len(outcomes)
        report = {
            "benchmark": "longitudinal-change-safeguards-v1",
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
