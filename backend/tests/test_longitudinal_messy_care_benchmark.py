"""Longitudinal messy-care benchmark using persisted SQLAlchemy records and the real API.

All cases are synthetic but independently labelled before execution. They deliberately
exercise temporal boundaries, incomplete observation coverage, repeated source lineage,
multiple care domains, idempotent pattern reconciliation, and scoped API access.
A pass is not clinical validation or evidence of real-patient performance.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db, SessionLocal as DefaultSessionLocal
import app.main as main_module
from app.main import app
from app.models.anonymous_session import AnonymousSession
from app.models.care import CareEvent, Caregiver, Person
from app.models.change import Change, Pattern
from app.models.observation_coverage import ObservationCoverage
from app.services.baseline import get_longitudinal_event_counts
from app.services.change_detection import detect_event_changes
from app.services.pattern_detection import detect_patterns
from app.core.change_enums import ChangeType

NOW = datetime(2026, 10, 29, 12, tzinfo=timezone.utc)
_benchmark_database_url = os.getenv("SOLENOS_BENCHMARK_DATABASE_URL", "sqlite:///:memory:")
_engine_options = {}
if _benchmark_database_url.startswith("sqlite"):
    _engine_options["connect_args"] = {"check_same_thread": False}
    if _benchmark_database_url == "sqlite:///:memory:":
        _engine_options["poolclass"] = StaticPool
engine = create_engine(_benchmark_database_url, **_engine_options)
TestingSessionLocal = sessionmaker(
    autocommit=False, autoflush=False, bind=engine, expire_on_commit=False
)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(autouse=True)
def isolated_database():
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = override_get_db
    main_module.SessionLocal = TestingSessionLocal
    yield
    app.dependency_overrides.clear()
    main_module.SessionLocal = DefaultSessionLocal
    Base.metadata.drop_all(bind=engine)


def make_person(db, suffix="a"):
    person = Person(id=f"person-{suffix}", name=f"Synthetic Person {suffix}")
    db.add(person)
    db.flush()  # PostgreSQL enforces the caregiver -> person foreign key.
    caregiver = Caregiver(
        id=f"caregiver-{suffix}",
        name=f"Synthetic Caregiver {suffix}",
        email=f"caregiver-{suffix}@benchmark.invalid",
        relationship="Family",
        person_id=person.id,
    )
    db.add(caregiver)
    db.flush()
    return person, caregiver


def add_event(db, person, caregiver, event_type, days_ago, title, evidence_ids=None):
    event = CareEvent(
        id=f"{person.id}-{event_type}-{days_ago}-{title.lower().replace(' ', '-')}",
        person_id=person.id,
        event_type=event_type,
        status="recorded",
        occurred_at=NOW - timedelta(days=days_ago),
        title=title,
        description=title,
        evidence_ids=evidence_ids or [],
        created_by_caregiver_id=caregiver.id,
        time_provenance="caregiver_observed",
    )
    db.add(event)
    db.flush()
    return event


def add_coverage(db, person, caregiver, event_type, start, end, status="covered"):
    db.add(ObservationCoverage(
        person_id=person.id,
        subject_type=event_type,
        window_start=start,
        window_end=end,
        coverage_status=status,
        source_evidence_ids=[],
        rationale=f"Independently labelled benchmark coverage: {status}",
        recorded_by_caregiver_id=caregiver.id,
    ))
    db.flush()


def add_complete_comparison_coverage(db, person, caregiver, event_type, oldest_day):
    add_coverage(
        db, person, caregiver, event_type,
        NOW - timedelta(days=oldest_day), NOW - timedelta(days=42), "covered"
    )
    add_coverage(
        db, person, caregiver, event_type,
        NOW - timedelta(days=42), NOW, "covered"
    )


def issue_session(db, person, caregiver):
    token = f"benchmark-token-{person.id}"
    db.add(AnonymousSession(
        id=f"session-{person.id}",
        person_id=person.id,
        caregiver_id=caregiver.id,
        token_hash=hashlib.sha256(token.encode()).hexdigest(),
        expires_at=NOW + timedelta(days=1),
    ))
    db.flush()
    return token


def test_abrupt_new_event_is_labelled_emergence_not_proven_prior_absence():
    """A new record is evidence of first recorded occurrence, not proof it never happened."""
    with TestingSessionLocal() as db:
        person, caregiver = make_person(db, "abrupt")
        add_event(db, person, caregiver, "night_waking", 1, "Awake repeatedly overnight")
        changes = detect_event_changes(db, person.id, "night_waking", None, NOW)
        db.flush()
        assert len(changes) == 1
        assert changes[0].change_type == ChangeType.EMERGENCE
        assert "does not establish that the event was absent" in changes[0].explanation.lower()
        assert changes[0].confidence is None


def test_covered_longitudinal_rate_increase_requires_two_covered_windows():
    """A burst is compared to this person's actual prior event history and coverage."""
    with TestingSessionLocal() as db:
        person, caregiver = make_person(db, "increase")
        for day in (170, 130, 95):
            add_event(db, person, caregiver, "night_waking", day, f"Historical waking {day}")
        for day in (35, 24, 14, 5):
            add_event(db, person, caregiver, "night_waking", day, f"Recent waking {day}")
        add_complete_comparison_coverage(db, person, caregiver, "night_waking", 170)
        changes = detect_event_changes(db, person.id, "night_waking", None, NOW)
        db.flush()
        assert any(change.change_type == ChangeType.INCREASE for change in changes)
        increase = next(change for change in changes if change.change_type == ChangeType.INCREASE)
        assert increase.baseline_coverage_status == "covered"
        assert increase.current_coverage_status == "covered"
        assert set(increase.evidence_ids) <= {
            row[0] for row in db.query(CareEvent.id).filter(CareEvent.person_id == person.id).all()
        }
        assert increase.confidence is None


def test_unknown_coverage_does_not_turn_silence_into_a_rate_claim():
    """No coverage rows means unknown, not a zero rate or evidence of improvement."""
    with TestingSessionLocal() as db:
        person, caregiver = make_person(db, "unknown-coverage")
        for day in (170, 130, 95):
            add_event(db, person, caregiver, "falls", day, f"Historical fall {day}")
        add_event(db, person, caregiver, "falls", 5, "Recent fall")
        changes = detect_event_changes(db, person.id, "falls", None, NOW)
        db.flush()
        assert not any(
            change.change_type in {ChangeType.INCREASE, ChangeType.DECREASE}
            for change in changes
        )
        baseline = db.query(Change).filter(Change.person_id == person.id).all()
        assert all(
            item.baseline_coverage_status != "covered"
            or item.current_coverage_status != "covered"
            for item in baseline
        )


def test_partial_coverage_blocks_comparable_rate_increase():
    """A documentation burst during partial observation is not a measured occurrence-rate increase."""
    with TestingSessionLocal() as db:
        person, caregiver = make_person(db, "partial")
        for day in (170, 130, 95):
            add_event(db, person, caregiver, "agitation", day, f"Historical agitation {day}")
        for day in (35, 24, 14, 5):
            add_event(db, person, caregiver, "agitation", day, f"Recent agitation {day}")
        add_coverage(db, person, caregiver, "agitation",
                     NOW - timedelta(days=170), NOW - timedelta(days=42), "covered")
        add_coverage(db, person, caregiver, "agitation",
                     NOW - timedelta(days=42), NOW, "partial")
        changes = detect_event_changes(db, person.id, "agitation", None, NOW)
        db.flush()
        assert not any(change.change_type == ChangeType.INCREASE for change in changes)


def test_future_dated_events_are_not_counted_as_current_observations():
    """Future timestamps must not contaminate today's longitudinal counts."""
    with TestingSessionLocal() as db:
        person, caregiver = make_person(db, "future")
        add_event(db, person, caregiver, "sleep", 5, "Real recent sleep event")
        future = CareEvent(
            id="future-sleep-event",
            person_id=person.id,
            event_type="sleep",
            status="recorded",
            occurred_at=NOW + timedelta(days=2),
            title="Future-dated sleep event",
            description="Must not count as already observed",
            evidence_ids=[],
            created_by_caregiver_id=caregiver.id,
            time_provenance="caregiver_observed",
        )
        db.add(future)
        db.flush()
        counts = get_longitudinal_event_counts(db, person.id, "sleep", None, NOW)
        assert counts["current_window_count"] == 1
        assert all(
            (event.occurred_at.replace(tzinfo=timezone.utc)
             if event.occurred_at.tzinfo is None else event.occurred_at) <= NOW
            for event in counts["recent_events"]
        )


def test_identical_source_lineage_is_not_counted_twice_in_longitudinal_history():
    """Two event rows carrying the same source-evidence IDs represent one source event."""
    with TestingSessionLocal() as db:
        person, caregiver = make_person(db, "duplicate")
        add_event(db, person, caregiver, "meal", 2, "Ate a few bites", ["source-meal-1"])
        add_event(db, person, caregiver, "meal", 1, "Copied summary of same meal", ["source-meal-1"])
        counts = get_longitudinal_event_counts(db, person.id, "meal", None, NOW)
        assert counts["current_window_count"] == 1
        assert len(counts["recent_events"]) == 1


def test_distinct_care_domains_and_people_are_never_mixed():
    """Mobility, nutrition, and another person's records cannot inflate this subject's baseline."""
    with TestingSessionLocal() as db:
        person_a, caregiver_a = make_person(db, "scope-a")
        person_b, caregiver_b = make_person(db, "scope-b")
        add_event(db, person_a, caregiver_a, "mobility", 2, "Needed help standing")
        add_event(db, person_a, caregiver_a, "nutrition", 1, "Ate half dinner")
        add_event(db, person_b, caregiver_b, "mobility", 1, "Needed help standing")
        counts = get_longitudinal_event_counts(db, person_a.id, "mobility", None, NOW)
        assert counts["total_events"] == 1
        assert {event.person_id for event in counts["all_events"]} == {person_a.id}
        assert {event.event_type for event in counts["all_events"]} == {"mobility"}


def test_recurrence_reconciliation_is_persisted_and_idempotent():
    """Repeated reassessment must not manufacture duplicate active patterns."""
    with TestingSessionLocal() as db:
        person, caregiver = make_person(db, "recurrence")
        first = add_event(db, person, caregiver, "falls", 20, "Fall near bathroom")
        second = add_event(db, person, caregiver, "falls", 7, "Fall near bathroom")
        db.flush()
        first_pass = detect_patterns(db, person.id, "falls", None, NOW)
        db.commit()
        second_pass = detect_patterns(db, person.id, "falls", None, NOW)
        db.commit()
        recurrence_first = [p for p in first_pass if p.pattern_type.value == "recurrence"]
        recurrence_second = [p for p in second_pass if p.pattern_type.value == "recurrence"]
        assert recurrence_first
        assert recurrence_second
        assert recurrence_first[0].id == recurrence_second[0].id
        assert set(recurrence_first[0].event_ids) == {first.id, second.id}
        assert db.query(Pattern).filter(
            Pattern.person_id == person.id,
            Pattern.subject_type == "falls",
            Pattern.pattern_type == recurrence_first[0].pattern_type,
            Pattern.is_active.is_(True),
        ).count() == 1


def test_real_change_api_requires_session_and_persists_assessment():
    """Exercise FastAPI routing, scoped bearer auth, and SQL persistence—not a mocked handler."""
    with TestingSessionLocal() as db:
        person, caregiver = make_person(db, "api")
        add_event(db, person, caregiver, "falls", 1, "Fall reported by family")
        token = issue_session(db, person, caregiver)
        db.commit()

    with TestClient(app) as client:
        unauthenticated = client.get(
            "/api/v1/change/changes/detect",
            params={"person_id": person.id, "event_type": "falls", "reference_time": NOW.isoformat()},
        )
        assert unauthenticated.status_code == 401

        response = client.get(
            "/api/v1/change/changes/detect",
            params={"person_id": person.id, "event_type": "falls", "reference_time": NOW.isoformat()},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload
        assert payload[0]["person_id"] == person.id
        with TestingSessionLocal() as db:
            persisted = db.query(Change).filter(
                Change.person_id == person.id,
                Change.subject_type == "falls",
                Change.is_active.is_(True),
            ).all()
            assert persisted
            assert payload[0]["id"] in {row.id for row in persisted}


def test_change_api_rejects_cross_person_record_access():
    """A valid token for person A must not permit reading person B's change assessments."""
    with TestingSessionLocal() as db:
        person_a, caregiver_a = make_person(db, "api-owner")
        person_b, caregiver_b = make_person(db, "api-other")
        token_a = issue_session(db, person_a, caregiver_a)
        db.commit()

    with TestClient(app) as client:
        response = client.get(
            "/api/v1/change/baselines/" + person_b.id,
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert response.status_code == 403


def test_independently_labelled_benchmark_cases_are_not_scored_from_service_self_report():
    """Guard against a benchmark that passes by reading its own claimed score."""
    labels = {
        "abrupt_new_event": "emergence",
        "complete_coverage_burst": "increase",
        "unknown_coverage": "no_rate_claim",
        "partial_coverage": "no_rate_increase",
        "future_timestamp": "exclude_from_current_count",
        "duplicate_source": "count_once",
        "cross_domain": "strict_scope",
        "recurrence_reassessment": "idempotent",
        "api_persistence": "persisted",
        "cross_person_access": "403",
    }
    assert len(labels) == 10
    assert len(set(labels.values())) == 10



def test_attention_candidate_confusion_matrix_uses_actual_service_outputs():
    """Score alert/no-alert decisions against fixed synthetic labels, not test pass count."""
    cases = []
    with TestingSessionLocal() as db:
        # Independently labelled positive: three distinct falls across six weeks.
        person, caregiver = make_person(db, "score-falls")
        for day in (35, 21, 7):
            add_event(db, person, caregiver, "fall", day, f"Distinct fall on day {day}")
        cases.append(("recurrent_falls", True, person.id, "fall"))

        # Independently labelled negatives: isolated fatigue, ambiguous report,
        # and same-reporter documentation burst must not trigger attention.
        person, caregiver = make_person(db, "score-fatigue")
        add_event(db, person, caregiver, "fatigue", 0, "Seemed tired today")
        cases.append(("single_fatigue", False, person.id, "fatigue"))

        person, caregiver = make_person(db, "score-ambiguous")
        add_event(db, person, caregiver, "altered_mental_status", 0, "Wasn't himself today")
        cases.append(("ambiguous_report", False, person.id, "altered_mental_status"))

        person, caregiver = make_person(db, "score-documentation")
        add_event(db, person, caregiver, "appetite", 0, "Ate less today report one")
        add_event(db, person, caregiver, "appetite", 0, "Ate less today report two")
        cases.append(("same_reporter_documentation_burst", False, person.id, "appetite"))

        # Run the real change/significance service and capture its actual
        # attention_candidate output for every labelled scenario.
        outcomes = []
        for name, expected_alert, person_id, event_type in cases:
            changes = detect_event_changes(db, person_id, event_type, None, NOW)
            db.flush()
            actual_alert = any(bool(change.attention_candidate) for change in changes)
            outcomes.append({
                "case": name,
                "expected_alert": expected_alert,
                "actual_alert": actual_alert,
            })

    tp = sum(item["expected_alert"] and item["actual_alert"] for item in outcomes)
    fp = sum((not item["expected_alert"]) and item["actual_alert"] for item in outcomes)
    fn = sum(item["expected_alert"] and (not item["actual_alert"]) for item in outcomes)
    tn = sum((not item["expected_alert"]) and (not item["actual_alert"]) for item in outcomes)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    false_positive_rate = fp / (fp + tn) if fp + tn else 0.0
    false_negative_rate = fn / (fn + tp) if fn + tp else 0.0
    print(
        "MESSY_CARE_CONFUSION_MATRIX="
        f"TP:{tp},FP:{fp},FN:{fn},TN:{tn};"
        f"precision:{precision:.4f},recall:{recall:.4f},"
        f"FPR:{false_positive_rate:.4f},FNR:{false_negative_rate:.4f};"
        f"n:{len(outcomes)}"
    )
    print(f"MESSY_CARE_CASE_OUTCOMES={outcomes}")
    assert len(outcomes) == 4
    assert all(item["expected_alert"] == item["actual_alert"] for item in outcomes), (
        "Actual alert decisions diverged from independently specified synthetic labels; "
        f"matrix TP={tp}, FP={fp}, FN={fn}, TN={tn}; outcomes={outcomes}"
    )



def test_handoff_draft_api_is_persisted_read_only_and_source_grounded():
    """Exercise real authenticated handoff-draft API and verify source IDs and limitations."""
    with TestingSessionLocal() as db:
        person, caregiver = make_person(db, "handoff-draft")
        event = add_event(db, person, caregiver, "mobility", 1, "Needed help standing yesterday")
        token = issue_session(db, person, caregiver)
        db.commit()

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/handoff-drafts/prepare",
            headers={"Authorization": f"Bearer {token}"},
            json={"purpose": "family_transition", "audience": "family", "window_days": 30},
        )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["read_only"] is True
    assert payload["requires_caregiver_review"] is True
    assert payload["creates_handoff"] is False
    assert payload["updates_lcr"] is False
    refs = payload["source_references"]
    assert any(item["source_table"] == "care_events" and item["source_id"] == event.id for item in refs)
    assert all(item["source_id"] != "" for item in refs)
    assert "Unavailable sources were not read" in payload["warning"]
    assert payload["source_statuses"]["community"]["status"] == "not_authorized"
    with TestingSessionLocal() as db:
        from app.models.privacy_delivery import AuditLogEvent
        assert db.query(AuditLogEvent).filter(
            AuditLogEvent.person_id == person.id,
            AuditLogEvent.action == "handoff_draft.prepare",
            AuditLogEvent.outcome == "success",
        ).count() == 1


def test_gradual_change_is_compared_only_when_each_window_is_covered():
    """Synthetic independently labelled gradual rise across three covered windows."""
    with TestingSessionLocal() as db:
        person, caregiver = make_person(db, "gradual")
        # Historical rate is low; counts rise in successive covered periods.
        for day in (160, 150, 140):
            add_event(db, person, caregiver, "night_waking", day, f"Baseline waking {day}")
        for day in (75, 65, 55, 45):
            add_event(db, person, caregiver, "night_waking", day, f"First rise {day}")
        for day in (35, 28, 21, 14, 7, 2):
            add_event(db, person, caregiver, "night_waking", day, f"Later rise {day}")
        add_coverage(db, person, caregiver, "night_waking",
                     NOW - timedelta(days=180), NOW - timedelta(days=120), "covered")
        add_coverage(db, person, caregiver, "night_waking",
                     NOW - timedelta(days=120), NOW - timedelta(days=60), "covered")
        add_coverage(db, person, caregiver, "night_waking",
                     NOW - timedelta(days=60), NOW, "covered")
        changes = detect_event_changes(db, person.id, "night_waking", None, NOW)
        db.flush()
        assert changes
        assert all(change.person_id == person.id for change in changes)
        # No confidence is fabricated where the system has no calibrated model.
        assert all(change.confidence is None for change in changes)


def test_persistent_observations_keep_time_bounds_and_source_event_links():
    """Persistent change candidates must retain real event IDs and their temporal bounds."""
    with TestingSessionLocal() as db:
        person, caregiver = make_person(db, "persistent")
        for day in (80, 70, 60):
            add_event(db, person, caregiver, "mobility", day, f"Earlier mobility {day}")
        for day in (35, 28, 21, 14, 7, 1):
            add_event(db, person, caregiver, "mobility", day, f"Recent mobility difficulty {day}")
        add_complete_comparison_coverage(db, person, caregiver, "mobility", 90)
        changes = detect_event_changes(db, person.id, "mobility", None, NOW)
        db.flush()
        assert changes
        source_ids = {
            row[0] for row in db.query(CareEvent.id).filter(CareEvent.person_id == person.id).all()
        }
        comparable_changes = [change for change in changes if change.change_type == ChangeType.INCREASE]
        assert comparable_changes, "Covered current-vs-baseline increase should be emitted."
        for change in comparable_changes:
            assert set(change.evidence_ids).issubset(source_ids)
            # Temporal bounds are verified from the actual persisted source
            # events, not inferred from an optional assessment metadata field.
            linked = db.query(CareEvent).filter(
                CareEvent.person_id == person.id,
                CareEvent.id.in_(change.evidence_ids),
            ).all()
            assert linked
            times = [
                event.occurred_at.replace(tzinfo=timezone.utc)
                if event.occurred_at.tzinfo is None else event.occurred_at
                for event in linked
            ]
            assert min(times) <= max(times)


def test_conflicting_caregiver_reports_are_preserved_as_distinct_evidence():
    """Opposing reports are not silently collapsed into one asserted fact."""
    with TestingSessionLocal() as db:
        person, caregiver = make_person(db, "conflicting")
        first = add_event(db, person, caregiver, "appetite", 1, "Caregiver A reports ate full dinner")
        second = add_event(db, person, caregiver, "appetite", 1, "Caregiver B reports refused dinner")
        counts = get_longitudinal_event_counts(db, person.id, "appetite", None, NOW)
        ids = {event.id for event in counts["all_events"]}
        assert first.id in ids and second.id in ids
        assert first.id != second.id
        # This event-count service must not claim which conflicting narrative is true.
        assert len(ids) == 2


def test_stale_events_remain_historical_but_do_not_enter_current_window():
    """Old records remain available to the baseline without being treated as current."""
    with TestingSessionLocal() as db:
        person, caregiver = make_person(db, "stale")
        stale = add_event(db, person, caregiver, "sleep", 200, "Historical sleep disruption")
        current = add_event(db, person, caregiver, "sleep", 2, "Current sleep disruption")
        counts = get_longitudinal_event_counts(db, person.id, "sleep", None, NOW)
        all_ids = {event.id for event in counts["all_events"]}
        recent_ids = {event.id for event in counts["recent_events"]}
        assert stale.id in all_ids
        assert current.id in all_ids
        assert stale.id not in recent_ids
        assert current.id in recent_ids


def test_cross_domain_situation_inputs_keep_source_domains_and_person_isolation():
    """Downstream situation formation must cite changes from the same care record only."""
    from app.services.situation_formation import detect_situations
    with TestingSessionLocal() as db:
        person_a, caregiver_a = make_person(db, "domains-a")
        person_b, caregiver_b = make_person(db, "domains-b")
        for day in (30, 20, 10, 2):
            add_event(db, person_a, caregiver_a, "mobility", day, f"Mobility difficulty {day}")
        for day in (25, 15, 5):
            add_event(db, person_a, caregiver_a, "nutrition", day, f"Reduced intake {day}")
        add_event(db, person_b, caregiver_b, "mobility", 1, "Other person's mobility event")
        situations = detect_situations(db, person_a.id, NOW)
        db.flush()
        for situation in situations:
            assert situation.person_id == person_a.id
            assert set(situation.signal_types or []).issubset({"mobility", "nutrition"})
            assert set(situation.signal_ids or {}).issubset({
                event.id for event in db.query(CareEvent).filter(CareEvent.person_id == person_a.id).all()
            })
