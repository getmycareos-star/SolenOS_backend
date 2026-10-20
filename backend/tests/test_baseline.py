"""Behavioral tests for personal historical baselines.

Each test uses the shared isolated database fixture; importing this module must
not create a database or share persistent state across test cases.
"""
from datetime import datetime, timedelta, timezone

from app.core.change_enums import BaselineConfidence, BaselineType
from app.core.event_enums import CareEventStatus, CareEventType
from app.models.caregiver import Caregiver
from app.models.care_event import CareEvent
from app.models.change import Baseline
from app.models.identity import Person
from app.services.baseline import (
    build_baseline,
    compare_to_baseline,
    get_longitudinal_event_counts,
)


def _add_person_and_caregiver(db, person_id: str):
    person = Person(id=person_id, name="Test Person")
    caregiver = Caregiver(
        id=f"caregiver-{person_id}",
        name="Test Caregiver",
        email=f"{person_id}@example.com",
        relationship="family",
        person_id=person_id,
        timezone="UTC",
    )
    db.add_all([person, caregiver])
    db.commit()
    return person, caregiver


def _add_event(db, person, caregiver, event_time, index, subject_id=None):
    event = CareEvent(
        person_id=person.id,
        subject_id=subject_id or person.id,
        event_type=CareEventType.FALL,
        status=CareEventStatus.RECORDED,
        event_time=event_time,
        title=f"Fall {index}",
        original_expression=f"Fall observation {index}",
        created_by_caregiver_id=caregiver.id,
    )
    db.add(event)
    db.flush()
    return event


def test_build_baseline_uses_high_confidence_only_after_five_historical_events(db):
    person, caregiver = _add_person_and_caregiver(db, "person-baseline-high")
    now = datetime.now(timezone.utc)
    for i in range(5):
        _add_event(db, person, caregiver, now - timedelta(days=200 + i * 30), i + 1)
    db.commit()

    baseline = build_baseline(db, person.id, CareEventType.FALL, None, now)

    assert baseline is not None
    assert baseline.event_count == 5
    assert baseline.confidence == BaselineConfidence.HIGH
    assert baseline.person_id == person.id
    assert len(baseline.evidence_ids) == 0


def test_baseline_confidence_is_moderate_for_two_to_four_historical_events(db):
    person, caregiver = _add_person_and_caregiver(db, "person-baseline-moderate")
    now = datetime.now(timezone.utc)
    for i in range(3):
        _add_event(db, person, caregiver, now - timedelta(days=100 + i * 20), i + 1)
    db.commit()

    baseline = build_baseline(db, person.id, CareEventType.FALL, None, now)

    assert baseline is not None
    assert baseline.event_count == 3
    assert baseline.confidence == BaselineConfidence.MODERATE


def test_baseline_confidence_is_low_for_one_historical_event(db):
    person, caregiver = _add_person_and_caregiver(db, "person-baseline-low")
    now = datetime.now(timezone.utc)
    _add_event(db, person, caregiver, now - timedelta(days=100), 1)
    db.commit()

    baseline = build_baseline(db, person.id, CareEventType.FALL, None, now)

    assert baseline is not None
    assert baseline.event_count == 1
    assert baseline.confidence == BaselineConfidence.LOW


def test_compare_to_baseline_uses_explicit_current_window_length():
    now = datetime.now(timezone.utc)
    baseline = Baseline(
        person_id="person-compare",
        subject_type=CareEventType.FALL,
        subject_id="",
        baseline_type=BaselineType.PERSONAL_HISTORICAL,
        window_start=now - timedelta(days=365),
        window_end=now - timedelta(days=42),
        event_count=10,
        frequency_per_period=10.0,
        state_value=None,
        confidence=BaselineConfidence.HIGH,
        is_stable=True,
        observation_density=10.0 / 365.0,
        documentation_bias_flag=False,
        evidence_ids=[],
    )

    result = compare_to_baseline(baseline, current_count=15, current_window_days=30)

    assert result["baseline_event_count"] == 10
    assert result["current_window_days"] == 30
    assert result["relative_change_percent"] == 50.0
    assert result["baseline_confidence"] == "high"


def test_longitudinal_counts_separate_recent_and_historical_events(db):
    person, caregiver = _add_person_and_caregiver(db, "person-counts")
    now = datetime.now(timezone.utc)
    event_days = [10, 15, 160, 190, 220]
    for index, days_ago in enumerate(event_days, start=1):
        _add_event(db, person, caregiver, now - timedelta(days=days_ago), index)
    db.commit()

    counts = get_longitudinal_event_counts(db, person.id, CareEventType.FALL, None, now)

    assert counts["current_window_count"] == 2
    assert counts["historical_count"] == 3
    assert len(counts["recent_events"]) == 2


def test_subject_filter_matches_subject_id_not_event_id(db):
    person, caregiver = _add_person_and_caregiver(db, "person-subject-owner")
    subject = Person(id="person-other-subject", name="Other Subject")
    db.add(subject)
    db.commit()
    now = datetime.now(timezone.utc)
    _add_event(db, person, caregiver, now - timedelta(days=100), 1, subject_id=person.id)
    _add_event(db, person, caregiver, now - timedelta(days=110), 2, subject_id=subject.id)
    db.commit()

    counts = get_longitudinal_event_counts(db, person.id, CareEventType.FALL, person.id, now)

    assert counts["total_events"] == 1
    assert counts["all_events"][0].subject_id == person.id
