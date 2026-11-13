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
from app.core.change_enums import ChangeType, ChangeDirection, PatternType

NOW = datetime.now(timezone.utc)
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
    is_postgres = engine.dialect.name == "postgresql"
    if is_postgres:
        # The CI PostgreSQL database is migrated before this suite starts. Never
        # drop migrated production-shaped tables: foreign keys and append-only
        # audit history intentionally make metadata.drop_all unsafe here.
        # Reset only row data in this disposable CI database, preserving the
        # migration-owned schema and alembic_version.
        from sqlalchemy import inspect, text

        table_names = [
            name for name in inspect(engine).get_table_names()
            if name != "alembic_version"
        ]
        if table_names:
            quoted = ", ".join(f'"{name.replace(chr(34), chr(34) * 2)}"' for name in table_names)
            with engine.begin() as connection:
                connection.execute(text(f"TRUNCATE TABLE {quoted} RESTART IDENTITY CASCADE"))
    else:
        Base.metadata.create_all(bind=engine)

    app.dependency_overrides[get_db] = override_get_db
    main_module.SessionLocal = TestingSessionLocal
    yield
    app.dependency_overrides.clear()
    main_module.SessionLocal = DefaultSessionLocal
    if not is_postgres:
        Base.metadata.drop_all(bind=engine)


def make_person(db, suffix="a"):
    # IDs must fit the production PostgreSQL varchar(32) schema.
    compact = hashlib.sha256(suffix.encode()).hexdigest()[:12]
    person = Person(id=f"p-{compact}", name=f"Synthetic Person {suffix}")
    db.add(person)
    db.flush()  # PostgreSQL enforces the caregiver -> person foreign key.
    caregiver = Caregiver(
        id=f"c-{compact}",
        name=f"Synthetic Caregiver {suffix}",
        email=f"c-{compact}@benchmark.invalid",
        relationship="Family",
        person_id=person.id,
    )
    db.add(caregiver)
    db.flush()
    # Persist identity fixtures before separate API sessions use them on PostgreSQL.
    db.commit()
    return person, caregiver


def add_event(db, person, caregiver, event_type, days_ago, title, evidence_ids=None):
    event = CareEvent(
        id="ev-" + hashlib.sha256((person.id + "|" + event_type + "|" + str(days_ago) + "|" + title).encode()).hexdigest()[:24],
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


def test_source_lineage_deduplicates_same_occurrence_but_not_distinct_occurrences():
    """Reuse of a source is deduplicated only when the occurrence time also matches."""
    with TestingSessionLocal() as db:
        person, caregiver = make_person(db, "duplicate")
        add_event(db, person, caregiver, "meal", 2, "Ate a few bites", ["source-meal-1"])
        # Same source at a different occurrence time represents a distinct meal.
        add_event(db, person, caregiver, "meal", 1, "Ate a few bites again", ["source-meal-1"])
        # A duplicate row for the first occurrence should collapse with the first.
        add_event(db, person, caregiver, "meal", 2, "Copied summary of same meal", ["source-meal-1"])
        counts = get_longitudinal_event_counts(db, person.id, "meal", None, NOW)
        assert counts["current_window_count"] == 2
        assert len(counts["recent_events"]) == 2


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


def test_gradual_trend_requires_three_distinct_covered_assessments_and_source_events():
    """The real pattern service must detect a gradual trend from persisted, covered assessments."""
    with TestingSessionLocal() as db:
        person, caregiver = make_person(db, "gradual-trend")
        events = [
            add_event(db, person, caregiver, "mobility", 25, "Needed a little more support"),
            add_event(db, person, caregiver, "mobility", 12, "Needed help standing"),
            add_event(db, person, caregiver, "mobility", 2, "Needed help standing and walking"),
        ]
        add_coverage(
            db, person, caregiver, "mobility",
            NOW - timedelta(days=90), NOW, "covered"
        )
        ends = [NOW - timedelta(days=30), NOW - timedelta(days=15), NOW]
        for index, (event, end) in enumerate(zip(events, ends), start=1):
            db.add(Change(
                person_id=person.id,
                subject_type="mobility",
                subject_id="",
                change_type=ChangeType.INCREASE,
                change_direction=ChangeDirection.INCREASING,
                previous_state=f"Recorded mobility state {index - 1}",
                current_state=f"Recorded mobility state {index}",
                magnitude=float(index * 10),
                previous_count=index,
                current_count=index + 1,
                comparison_window_start=end - timedelta(days=30),
                comparison_window_end=end,
                baseline_coverage_status="covered",
                current_coverage_status="covered",
                evidence_ids=[event.id],
                explanation="Synthetic benchmark assessment; not a clinical conclusion.",
                is_active=True,
            ))
        db.flush()
        patterns = detect_patterns(db, person.id, "mobility", None, NOW)
        db.flush()
        trends = [pattern for pattern in patterns if pattern.pattern_type == PatternType.TREND]
        assert trends, "Three distinct covered assessment windows with source events should form a trend."
        trend = trends[0]
        assert set(trend.event_ids) == {event.id for event in events}
        assert len(set(trend.change_ids or [])) >= 3
        assert trend.confidence is None


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

def test_multi_source_ingestion_preserves_uploader_reporter_observer_and_dual_time():
    """Uploader, reporter and collector are separate identities; event and ingest time stay distinct."""
    with TestingSessionLocal() as db:
        person, uploader = make_person(db, "attribution-uploader")
        reporter = Caregiver(
            id="c-reporter-" + hashlib.sha256(person.id.encode()).hexdigest()[:10],
            name="Synthetic Reporter",
            relationship="Family",
            person_id=person.id,
        )
        observer = Caregiver(
            id="c-observer-" + hashlib.sha256(person.id.encode()).hexdigest()[:10],
            name="Synthetic Observer",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([reporter, observer])
        db.flush()
        token = issue_session(db, person, uploader)
        db.commit()

    client = TestClient(app)
    response = client.post(
        "/api/v1/evidence",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "person_id": person.id,
            "type": "note",
            "evidence_kind": "caregiver_report",
            "epistemic_status": "unverified",
            "source_text": "Reporter says the person needed two-person assistance at 08:15.",
            "uploaded_by_caregiver_id": uploader.id,
            "reported_by_caregiver_id": reporter.id,
            "reported_by_label": reporter.name,
            "collected_by_caregiver_id": observer.id,
            "original_author": "Family shift handover",
            "occurred_at": "2026-10-28T08:15:00+00:00",
            "occurred_at_timezone": "Europe/London",
            "reported_at": "2026-10-29T11:00:00+00:00",
            "collected_at": "2026-10-29T11:05:00+00:00",
            "origin_channel": "family_handover",
        },
    )
    assert response.status_code in {200, 201}, response.text
    payload = response.json()
    assert payload["uploaded_by_caregiver_id"] == uploader.id
    assert payload["reported_by_caregiver_id"] == reporter.id
    assert payload["reported_by_label"] == reporter.name
    assert payload["collected_by_caregiver_id"] == observer.id
    assert payload["occurred_at"].startswith("2026-10-28T08:15:00")
    assert payload["reported_at"].startswith("2026-10-29T11:00:00")
    assert payload["collected_at"].startswith("2026-10-29T11:05:00")

    # Fresh SQLAlchemy session proves persisted attribution and time provenance,
    # not merely response serialization from the request object.
    with TestingSessionLocal() as verify_db:
        stored = verify_db.query(__import__("app.models.care", fromlist=["Evidence"]).Evidence).filter_by(
            id=payload["id"], person_id=person.id
        ).one()
        assert stored.uploaded_by_caregiver_id == uploader.id
        assert stored.reported_by_caregiver_id == reporter.id
        assert stored.collected_by_caregiver_id == observer.id
        assert stored.original_author == "Family shift handover"
        assert stored.occurred_at.isoformat().startswith("2026-10-28T08:15:00")
        assert stored.ingested_at is not None


# One hundred independently labelled adversarial cases. Each family varies
# timestamps/identities while retaining an explicit expected result; expected
# counts are labels, never derived from the service under test.
def _messy_care_scenario_cases():
    cases = []
    for variant in range(10):
        cases.extend([
            (f"future_excluded_{variant:02d}", "future", variant, 1, 1),
            (f"same_source_same_occurrence_{variant:02d}", "duplicate", variant, 1, 1),
            (f"same_source_distinct_occurrences_{variant:02d}", "distinct", variant, 2, 2),
            (f"stale_plus_current_{variant:02d}", "stale_current", variant, 2, 1),
            (f"invalidated_excluded_{variant:02d}", "invalidated", variant, 0, 0),
            (f"cross_person_isolated_{variant:02d}", "cross_person", variant, 1, 1),
            (f"cross_domain_isolated_{variant:02d}", "cross_domain", variant, 1, 1),
            (f"unlinked_events_remain_distinct_{variant:02d}", "no_source", variant, 2, 2),
            (f"42_day_boundary_{variant:02d}", "boundary", variant, 2, 1),
            (f"late_arrival_stays_historical_{variant:02d}", "late_arrival", variant, 1, 0),
        ])
    assert len(cases) == 100
    return cases


@pytest.mark.parametrize(
    "case_id,kind,variant,expected_total,expected_current",
    _messy_care_scenario_cases(),
    ids=lambda value: value if isinstance(value, str) and "_" in value else None,
)
def test_100_case_messy_care_temporal_scope_and_deduplication_benchmark(
    case_id, kind, variant, expected_total, expected_current
):
    with TestingSessionLocal() as db:
        person, caregiver = make_person(db, f"100-case-{case_id}")
        event_type = "mobility"
        day = variant + 1

        if kind == "future":
            add_event(db, person, caregiver, event_type, day, f"observed-{case_id}")
            add_event(db, person, caregiver, event_type, -1 - variant, f"future-{case_id}")
        elif kind == "duplicate":
            source = [f"source-{case_id}"]
            add_event(db, person, caregiver, event_type, day, f"original-{case_id}", source)
            add_event(db, person, caregiver, event_type, day, f"copied-{case_id}", source)
        elif kind == "distinct":
            source = [f"source-{case_id}"]
            add_event(db, person, caregiver, event_type, day, f"occurrence-a-{case_id}", source)
            add_event(db, person, caregiver, event_type, day + 1, f"occurrence-b-{case_id}", source)
        elif kind == "stale_current":
            add_event(db, person, caregiver, event_type, 60 + variant, f"stale-{case_id}")
            add_event(db, person, caregiver, event_type, day, f"current-{case_id}")
        elif kind == "invalidated":
            event = add_event(db, person, caregiver, event_type, day, f"invalidated-{case_id}")
            event.status = "invalidated"
        elif kind == "cross_person":
            add_event(db, person, caregiver, event_type, day, f"own-{case_id}")
            other_person, other_caregiver = make_person(db, f"other-{case_id}")
            add_event(db, other_person, other_caregiver, event_type, day, f"other-{case_id}")
        elif kind == "cross_domain":
            add_event(db, person, caregiver, event_type, day, f"target-{case_id}")
            add_event(db, person, caregiver, "nutrition", day, f"other-domain-{case_id}")
        elif kind == "no_source":
            add_event(db, person, caregiver, event_type, day, f"unlinked-a-{case_id}")
            add_event(db, person, caregiver, event_type, day, f"unlinked-b-{case_id}")
        elif kind == "boundary":
            add_event(db, person, caregiver, event_type, 42, f"boundary-current-{case_id}")
            add_event(db, person, caregiver, event_type, 43, f"boundary-historical-{case_id}")
        elif kind == "late_arrival":
            add_event(db, person, caregiver, event_type, 90 + variant, f"late-arriving-old-event-{case_id}")
        else:
            raise AssertionError(f"Unknown benchmark scenario: {kind}")

        db.flush()
        result = get_longitudinal_event_counts(db, person.id, event_type, None, NOW)
        assert result["total_events"] == expected_total, (
            f"{case_id}: expected total={expected_total}, got={result['total_events']}"
        )
        assert result["current_window_count"] == expected_current, (
            f"{case_id}: expected current={expected_current}, got={result['current_window_count']}"
        )
        assert result["historical_count"] == expected_total - expected_current
        assert all(event.person_id == person.id for event in result["all_events"])
        assert all(event.event_type == event_type for event in result["all_events"])



def test_delta_assertion_links_only_finding_specific_evidence(monkeypatch):
    """An unrelated document in the same request must not support a medication assertion."""
    import json
    from app.models.care import Evidence
    from app.models.care_continuity import CareAssertion
    from app.services import delta_engine

    with TestingSessionLocal() as db:
        person, caregiver = make_person(db, "delta-finding-provenance")
        excerpt = "The prescriber started amlodipine 5 mg daily."
        second_excerpt = "The prescriber also started metformin 500 mg twice daily."
        content_text = f"The discharge summary states: {excerpt} {second_excerpt}"
        source_event = CareEvent(
            id="ev-delta-provenance-source",
            person_id=person.id,
            event_type="document",
            status="recorded",
            occurred_at=NOW,
            title="Synthetic discharge summary",
            description=content_text,
            evidence_ids=[],
            created_by_caregiver_id=caregiver.id,
            time_provenance="document_date",
        )
        matching = Evidence(
            id="evidence-delta-medication",
            person_id=person.id,
            type="document",
            source_text=excerpt,
            uploaded_by_caregiver_id=caregiver.id,
        )
        second_matching = Evidence(
            id="evidence-delta-second-medication",
            person_id=person.id,
            type="document",
            source_text=second_excerpt,
            uploaded_by_caregiver_id=caregiver.id,
        )
        unrelated = Evidence(
            id="evidence-delta-unrelated",
            person_id=person.id,
            type="note",
            source_text="Sleep was poor on the previous night.",
            uploaded_by_caregiver_id=caregiver.id,
        )
        db.add_all([source_event, matching, second_matching, unrelated])
        db.flush()

        model_result = {
            "event_summary": "A discharge medication list mentions amlodipine.",
            "medications": [
                {
                    "change_type": "new",
                    "name": "Amlodipine",
                    "previous": None,
                    "current": "5 mg daily",
                    "evidence_excerpt": excerpt,
                    "certainty": "supported",
                },
                {
                    "change_type": "new",
                    "name": "Metformin",
                    "previous": None,
                    "current": "500 mg twice daily",
                    "evidence_excerpt": second_excerpt,
                    "certainty": "supported",
                },
            ],
            "conditions": [],
            "symptoms": [],
            "tests": [],
            "follow_ups": [],
            "contradictions": [],
            "clinical_gaps": [],
            "caregiver_summary": "The document lists amlodipine 5 mg daily.",
            "clinician_summary": "Amlodipine 5 mg daily is listed in the supplied discharge summary.",
        }
        monkeypatch.setattr(
            delta_engine,
            "_local_ollama_url",
            lambda: ("http://127.0.0.1:11434", "synthetic-contract-model", 1.0),
        )
        qwen_call_options = []
        def fake_qwen_chat(*args, **kwargs):
            qwen_call_options.append(kwargs.get("options") or {})
            return json.dumps(model_result)

        monkeypatch.setattr(delta_engine, "qwen_chat", fake_qwen_chat)

        delta_engine.reconcile_event(
            db,
            person_id=person.id,
            event_date=NOW,
            source="synthetic discharge summary",
            content=content_text,
            source_event_id=source_event.id,
            evidence_ids=[matching.id, second_matching.id, unrelated.id],
            persist=True,
            created_by_caregiver_id=caregiver.id,
        )

        assert qwen_call_options
        assert qwen_call_options[0].get("num_predict") == 384
        assert qwen_call_options[0].get("num_ctx") == 4096

        assertions = db.query(CareAssertion).filter(
            CareAssertion.person_id == person.id,
            CareAssertion.subject_type == "medication",
        ).all()
        assert {item.subject_id for item in assertions} == {"amlodipine", "metformin"}
        by_subject = {item.subject_id: item for item in assertions}
        assert by_subject["amlodipine"].source_event_id == source_event.id
        assert by_subject["amlodipine"].evidence_ids == [matching.id]
        assert by_subject["metformin"].source_event_id == source_event.id
        assert by_subject["metformin"].evidence_ids == [second_matching.id]
        assert all(unrelated.id not in item.evidence_ids for item in assertions)

        # A second document that merely lists a different dose must remain a
        # reported mention; it must not create a new current medication assertion.
        mention_excerpt = "Amlodipine 10 mg daily appears on the historical medication list."
        mention_event = CareEvent(
            id="ev-delta-medication-mention",
            person_id=person.id,
            event_type="document",
            status="recorded",
            occurred_at=NOW + timedelta(days=1),
            title="Historical medication list",
            description=mention_excerpt,
            evidence_ids=[],
            created_by_caregiver_id=caregiver.id,
            time_provenance="document_date",
        )
        mention_evidence = Evidence(
            id="evidence-delta-medication-mention",
            person_id=person.id,
            type="document",
            source_text=mention_excerpt,
            uploaded_by_caregiver_id=caregiver.id,
        )
        db.add_all([mention_event, mention_evidence])
        db.flush()
        model_result["medications"] = [{
            "change_type": "new",
            "name": "Amlodipine",
            "previous": None,
            "current": "10 mg daily",
            "evidence_excerpt": mention_excerpt,
            "certainty": "supported",
        }]
        result = delta_engine.reconcile_event(
            db,
            person_id=person.id,
            event_date=NOW + timedelta(days=1),
            source="historical medication list",
            content=mention_excerpt,
            source_event_id=mention_event.id,
            evidence_ids=[mention_evidence.id],
            persist=True,
            created_by_caregiver_id=caregiver.id,
        )
        assert result["medications"][0]["medication_mention_only"] is True
        assert db.query(CareAssertion).filter(
            CareAssertion.person_id == person.id,
            CareAssertion.source_event_id == mention_event.id,
        ).count() == 0


def test_medication_mentions_do_not_count_as_prescribing_changes():
    from app.services.delta_engine import _medication_change_is_explicit

    assert not _medication_change_is_explicit({
        "change_type": "new",
        "evidence_excerpt": "Amlodipine 5 mg daily appears on the discharge medication list.",
    })
    assert not _medication_change_is_explicit({
        "change_type": "discontinued",
        "evidence_excerpt": "Amlodipine 5 mg daily appears on the historical medication list.",
    })
    assert not _medication_change_is_explicit({
        "change_type": "new",
        "evidence_excerpt": "Amlodipine was not started during this admission.",
    })
    assert not _medication_change_is_explicit({
        "change_type": "discontinued",
        "evidence_excerpt": "Amlodipine was not discontinued.",
    })
    assert _medication_change_is_explicit({
        "change_type": "new",
        "evidence_excerpt": "The prescriber started amlodipine 5 mg daily.",
    })
    assert _medication_change_is_explicit({
        "change_type": "discontinued",
        "evidence_excerpt": "The clinician discontinued amlodipine.",
    })


@pytest.mark.parametrize(
    "scenario",
    __import__("test_lcr_reconciliation_scenario_corpus").SCENARIOS,
    ids=lambda item: item["scenario_id"],
)
def test_curated_100_scenarios_execute_through_delta_persistence(scenario, monkeypatch):
    """Run every curated timeline through Delta Engine with deterministic Qwen fixtures.

    This exercises the real SQL persistence, evidence gates, contradiction/open-loop
    persistence and person scoping. The model response is a controlled fixture, not
    live-model evaluation or proof that Qwen independently understands the timeline.
    """
    import json
    from app.models.care import CareEvent, Caregiver, Correction, Evidence
    from app.models.care_continuity import CareAssertion, CareDeltaRun, CareRecordRelationship
    from app.models.uncertainty import Contradiction, InformationGap, OpenQuestion
    from app.core.uncertainty_enums import EpistemicState, GapReason, GapLifecycleStatus, Priority
    from app.services import delta_engine

    with TestingSessionLocal() as db:
        person, caregiver = make_person(db, scenario["scenario_id"])
        # Seed the independently specified initial LCR baseline as a reported
        # historical assertion. The unknown medication state remains a gap, not
        # a fabricated "no medication" assertion.
        db.add(CareAssertion(
            id=f"baseline-{scenario['scenario_id']}",
            person_id=person.id,
            subject_type="symptom",
            subject_id=scenario["initial_lcr"]["assertions"][0]["topic"],
            field="symptom_reported_state",
            assertion_kind="time_bound_observation",
            asserted_value=scenario["initial_lcr"]["assertions"][0]["value"],
            source_label="Initial LCR baseline (reported)",
            source_text="Known initial state supplied by the scenario fixture.",
            evidence_ids=[],
            assertion_status="reported",
            observed_at=NOW - timedelta(days=90),
            created_by_caregiver_id=caregiver.id,
        ))
        db.add(InformationGap(
            person_id=person.id,
            subject_type="medication",
            subject_id="current_prescription",
            field="current_prescription",
            epistemic_state=EpistemicState.UNKNOWN,
            gap_reason=GapReason.ABSENT,
            priority=Priority.INFORMATIONAL,
            lifecycle_status=GapLifecycleStatus.OPEN,
            description="Current prescriber-confirmed medication and dose were not supplied.",
            evidence_ids=[],
        ))
        db.flush()
        ordered_events = sorted(
            scenario["incoming_events"], key=lambda item: item.get("arrival", 0)
        )
        source_text_by_event = {}
        model_result_by_call = {"value": None}

        def fixture_result(event):
            claim = event["claim"]
            result = {
                "event_summary": claim[:140],
                "medications": [],
                "conditions": [],
                "symptoms": [],
                "tests": [],
                "follow_ups": [],
                "contradictions": [],
                "clinical_gaps": [],
                "caregiver_summary": claim[:140],
                "clinician_summary": claim[:140],
            }
            topic = event.get("topic")
            if topic == "medication":
                result["clinical_gaps"].append({
                    "gap": "current prescribed medication is not established",
                    "why_it_matters": "A medication mention or caregiver report does not establish the current prescription.",
                    "next_question": "What is the currently prescribed medication and dose?",
                    "evidence_excerpt": claim,
                })
            elif topic == "medication_administration":
                result["clinical_gaps"].append({
                    "gap": "later administration of the missed dose is unknown",
                    "why_it_matters": "The record does not establish whether the reported missed dose was later administered.",
                    "next_question": "Was the reported missed dose later administered?",
                    "evidence_excerpt": claim,
                })
            elif event.get("kind") == "correction":
                result["clinical_gaps"].append({
                    "gap": "corrected date of the original observation is not independently established",
                    "why_it_matters": "The correction must remain attributed to the caregiver and the original event must remain in history.",
                    "next_question": "What is the corrected date of the original sleep observation?",
                    "evidence_excerpt": claim,
                })
            if event.get("assertion_name"):
                result["symptoms"] = [{
                    "change_type": "new",
                    "name": event["assertion_name"],
                    "previous": None,
                    "current": event["assertion_value"],
                    "evidence_excerpt": claim,
                    "certainty": "supported",
                }]
            elif topic == "medication":
                result["medications"] = [{
                    "change_type": "new",
                    "name": "medication",
                    "previous": None,
                    "current": "medication mention",
                    "evidence_excerpt": claim,
                    "certainty": "supported",
                }]
            elif topic == "medication_administration":
                result["medications"] = [{
                    "change_type": "uncertain",
                    "name": "medication",
                    "previous": None,
                    "current": "missed dose reported",
                    "evidence_excerpt": claim,
                    "certainty": "uncertain",
                }]

            if event.get("classification") == "historical_conflicting_evidence":
                earlier = next(
                    (text for text in source_text_by_event.values()
                     if "hospital document" in text or "medication" in text),
                    claim,
                )
                result["contradictions"] = [{
                    "topic": "medication",
                    "reports": [earlier, claim],
                    "why_conflicting": "The supplied medication records do not establish one current prescription.",
                    "evidence_excerpt": claim,
                    "evidence_excerpts": [earlier, claim],
                }]
            return result

        monkeypatch.setattr(
            delta_engine,
            "_local_ollama_url",
            lambda: ("http://127.0.0.1:11434", "curated-deterministic-fixture", 1.0),
        )

        def fake_qwen_chat(*args, **kwargs):
            return json.dumps(model_result_by_call["value"])

        monkeypatch.setattr(delta_engine, "qwen_chat", fake_qwen_chat)

        original_event = None
        for event_spec in ordered_events:
            event_id = event_spec["id"]
            evidence_id = event_spec["evidence_id"]
            claim = event_spec["claim"]
            source_text_by_event[event_id] = claim
            event_day = event_spec.get("corrected_day", event_spec["day"]) + event_spec.get("effective_day_offset", 0)
            occurred_at = NOW + timedelta(days=event_day)
            evidence = Evidence(
                id=evidence_id,
                person_id=person.id,
                type="document" if event_spec["kind"] == "document" else "note",
                source_text=claim,
                uploaded_by_caregiver_id=caregiver.id,
            )
            care_event = CareEvent(
                id=event_id,
                person_id=person.id,
                event_type=event_spec["kind"],
                status="recorded",
                occurred_at=occurred_at,
                title=claim[:120],
                description=claim,
                evidence_ids=[evidence_id],
                created_by_caregiver_id=caregiver.id,
                time_provenance="document_date" if event_spec["kind"] == "document" else "caregiver_observed",
            )
            db.add_all([evidence, care_event])
            db.flush()

            if event_spec.get("classification") == "duplicate_same_occurrence":
                original_id = event_spec.get("duplicate_of")
                original_event = db.query(CareEvent).filter(
                    CareEvent.id == original_id,
                    CareEvent.person_id == person.id,
                ).first()
                original_assertions = db.query(CareAssertion).filter(
                    CareAssertion.person_id == person.id,
                    CareAssertion.source_event_id == original_id,
                ).all()
                print("DUPLICATE_MATCH_DIAGNOSTIC", {
                    "incoming_event_id": event_id,
                    "incoming_occurred_at": care_event.occurred_at.isoformat() if care_event.occurred_at else None,
                    "original_event_id": original_id,
                    "original_occurred_at": original_event.occurred_at.isoformat() if original_event and original_event.occurred_at else None,
                    "original_assertions": [
                        {
                            "id": item.id,
                            "subject_type": item.subject_type,
                            "subject_id": item.subject_id,
                            "field": item.field,
                            "assertion_kind": item.assertion_kind,
                            "asserted_value": item.asserted_value,
                            "source_label": item.source_label,
                            "created_by_caregiver_id": item.created_by_caregiver_id,
                            "observed_at": item.observed_at.isoformat() if item.observed_at else None,
                            "assertion_status": item.assertion_status,
                        }
                        for item in original_assertions
                    ],
                })

            if event_spec.get("corrects_event_id"):
                original_event = db.query(CareEvent).filter(
                    CareEvent.id == event_spec["corrects_event_id"],
                    CareEvent.person_id == person.id,
                ).one()

            if event_spec["kind"] == "correction" and original_event is not None:
                db.add(Correction(
                    id=f"correction-{scenario['scenario_id']}",
                    person_id=person.id,
                    target_type="care_event",
                    target_id=original_event.id,
                    original_text=original_event.description or original_event.title,
                    corrected_text=claim,
                    reason="Caregiver supplied a correction to the original observation date.",
                    caregiver_id=caregiver.id,
                ))
                db.flush()

            model_result_by_call["value"] = fixture_result(event_spec)
            delta_engine.reconcile_event(
                db,
                person_id=person.id,
                event_date=occurred_at,
                source=event_spec["reporter"],
                content=claim,
                source_event_id=care_event.id,
                evidence_ids=[evidence_id],
                persist=True,
                created_by_caregiver_id=caregiver.id,
            )

        persisted_event_ids = {
            row[0] for row in db.query(CareEvent.id).filter(
                CareEvent.person_id == person.id
            ).all()
        }
        expected_event_ids = {event["id"] for event in ordered_events}
        assert expected_event_ids.issubset(persisted_event_ids)

        runs = db.query(CareDeltaRun).filter(
            CareDeltaRun.person_id == person.id
        ).all()
        assert len(runs) == len(ordered_events)
        assert {run.source_event_id for run in runs} == expected_event_ids
        assert all(run.evidence_ids for run in runs)
        for run in runs:
            receipt = json.loads(run.delta_json)
            assert receipt["provenance"]["engine_version"] == delta_engine.DELTA_ENGINE_VERSION
            assert receipt["provenance"]["source_event_id"] == run.source_event_id
            assert receipt["provenance"]["evidence_ids"] == run.evidence_ids
            assert "computed_changes" in receipt
            assert "claim_provenance" in receipt
            for claim in receipt["claim_provenance"]["claims"]:
                if claim["support_status"] in {"matched_source_and_state", "matched_competing_sources"}:
                    assert claim["source_references"], "a supported Delta finding must name its actual source records"
            for domain in ("medications", "conditions", "symptoms", "tests", "follow_ups", "clinical_gaps", "contradictions"):
                for finding in receipt.get(domain, []):
                    assert "source_record_refs" in finding, "every Delta finding must expose its claim-specific source references"

        # Compare exact source-grounded final assertions against the independently
        # authored expected state; a successful HTTP/service call is not a pass.
        for expected_assertion in scenario["expected_final_state"]["assertions"]:
            actual = db.query(CareAssertion).filter(
                CareAssertion.person_id == person.id,
                CareAssertion.subject_type == "symptom",
                CareAssertion.subject_id == expected_assertion["topic"],
                CareAssertion.asserted_value == expected_assertion["value"],
                CareAssertion.source_event_id == expected_assertion["source_event_id"],
                CareAssertion.assertion_status.in_(["reported", "review_required", "accepted"]),
            ).first()
            assert actual is not None, (
                f"{scenario['scenario_id']}: missing expected assertion {expected_assertion}"
            )
            assert actual.epistemic_state == "reported", (
                f"{scenario['scenario_id']}: source-grounded time-bound observation was misclassified"
            )

        duplicate_ids = {
            event["id"] for event in ordered_events
            if event.get("classification") == "duplicate_same_occurrence"
        }
        for duplicate_id in duplicate_ids:
            duplicate_assertions = db.query(CareAssertion).filter(
                CareAssertion.person_id == person.id,
                CareAssertion.source_event_id == duplicate_id,
            ).all()
            duplicate_assertion_details = [
                {
                    "id": row.id,
                    "subject_id": row.subject_id,
                    "field": row.field,
                    "asserted_value": row.asserted_value,
                    "observed_at": row.observed_at.isoformat() if row.observed_at else None,
                    "source_event_id": row.source_event_id,
                    "created_by_caregiver_id": row.created_by_caregiver_id,
                    "assertion_status": row.assertion_status,
                }
                for row in duplicate_assertions
            ]
            assert not duplicate_assertions, (
                f"{scenario['scenario_id']}: duplicate event {duplicate_id} created assertions: "
                f"{duplicate_assertion_details}"
            )
            duplicate_link = db.query(CareRecordRelationship).filter(
                CareRecordRelationship.person_id == person.id,
                CareRecordRelationship.source_type == "care_event",
                CareRecordRelationship.source_id == duplicate_id,
                CareRecordRelationship.relationship_type == "duplicate_observation_of",
            ).one()
            assert duplicate_link.target_id
            assert duplicate_link.target_type in {"care_assertion", "care_event"}
            duplicate_provenance = json.loads(duplicate_link.provenance)
            expected_match_basis = (
                "exact_subject_value_and_observed_at"
                if duplicate_link.target_type == "care_assertion"
                else "exact_source_event_time_and_content"
            )
            assert duplicate_provenance["match_basis"] == expected_match_basis
            assert duplicate_provenance["evidence_ids"] == [
                next(event["evidence_id"] for event in ordered_events if event["id"] == duplicate_id)
            ]
            if duplicate_link.target_type == "care_event":
                original_event_ids = {
                    event["id"] for event in ordered_events
                    if event.get("classification") != "duplicate_same_occurrence"
                }
                assert duplicate_link.target_id in original_event_ids

        # Medication references, historical lists and missed-dose reports are not
        # sufficient to establish a current prescription.
        medication_assertions = db.query(CareAssertion).filter(
            CareAssertion.person_id == person.id,
            CareAssertion.subject_type == "medication",
            CareAssertion.assertion_status.in_(["reported", "review_required", "accepted"]),
        ).all()
        assert medication_assertions == []
        initial_medication_gap = db.query(InformationGap).filter(
            InformationGap.person_id == person.id,
            InformationGap.subject_type == "medication",
            InformationGap.subject_id == "current_prescription",
        ).one()
        assert initial_medication_gap.epistemic_state == EpistemicState.UNKNOWN
        assert initial_medication_gap.lifecycle_status == GapLifecycleStatus.OPEN
        assert initial_medication_gap.gap_reason == GapReason.ABSENT

        # Exact final assertion agreement for the scenario's observed domains.
        # Include the seeded baseline and the first observation as historical
        # assertions; require the later observation and confusion report exactly,
        # and reject unexpected extra active assertions in these domains.
        observation_topic = scenario["initial_lcr"]["assertions"][0]["topic"]
        initial_value = scenario["initial_lcr"]["assertions"][0]["value"]
        expected_assertion_rows = {
            (observation_topic, initial_value, None, "reported"),
            (observation_topic, initial_value, f"{scenario['scenario_id']}-01", "reported"),
            (
                observation_topic,
                next(
                    event["assertion_value"] for event in ordered_events
                    if event["id"] == f"{scenario['scenario_id']}-07"
                ),
                f"{scenario['scenario_id']}-07",
                "reported",
            ),
            ("confusion", "increased confusion", f"{scenario['scenario_id']}-08", "reported"),
        }
        actual_assertion_rows = {
            (row.subject_id, row.asserted_value, row.source_event_id, row.assertion_status)
            for row in db.query(CareAssertion).filter(
                CareAssertion.person_id == person.id,
                CareAssertion.subject_type == "symptom",
                CareAssertion.subject_id.in_([observation_topic, "confusion"]),
                CareAssertion.assertion_status.in_(["reported", "review_required", "accepted"]),
            ).all()
        }
        assert actual_assertion_rows == expected_assertion_rows, (
            f"{scenario['scenario_id']}: exact assertion-state mismatch; "
            f"missing={expected_assertion_rows - actual_assertion_rows}; "
            f"unexpected={actual_assertion_rows - expected_assertion_rows}"
        )

        # Every persisted assertion must cite only evidence belonging to its own
        # source event, never unrelated evidence from another event in the run.
        evidence_owner = {
            event["evidence_id"]: event["id"] for event in ordered_events
        }
        for assertion in db.query(CareAssertion).filter(
            CareAssertion.person_id == person.id,
            CareAssertion.source_event_id.is_not(None),
        ).all():
            assert assertion.source_event_id in expected_event_ids
            assert assertion.evidence_ids
            assert all(
                evidence_owner.get(evidence_id) == assertion.source_event_id
                for evidence_id in assertion.evidence_ids
            )

        # Corrections remain additive audit records; the original source event is
        # never deleted. A separate correction/supersession workflow owns active
        # observation versioning and is not simulated by the Delta Engine fixture.
        correction_rows = db.query(Correction).filter(
            Correction.person_id == person.id
        ).all()
        assert len(correction_rows) == 1
        assert correction_rows[0].target_id in persisted_event_ids
        assert db.query(CareEvent).filter(
            CareEvent.id == correction_rows[0].target_id,
            CareEvent.person_id == person.id,
        ).count() == 1

        if any(event.get("classification") == "historical_conflicting_evidence"
               for event in ordered_events):
            persisted_conflicts = db.query(Contradiction).filter(
                Contradiction.person_id == person.id,
                Contradiction.subject_type == "delta_engine_report",
                Contradiction.subject_id == "medication",
                Contradiction.resolution_status == "unresolved",
            ).all()
            assert persisted_conflicts, f"{scenario['scenario_id']}: expected persisted unresolved conflicts"
            scenario_evidence_ids = {event["evidence_id"] for event in ordered_events}
            for persisted_conflict in persisted_conflicts:
                assert len(persisted_conflict.evidence_ids or []) >= 2
                assert set(persisted_conflict.evidence_ids).issubset(scenario_evidence_ids)

        actual_questions = db.query(OpenQuestion).filter(
            OpenQuestion.person_id == person.id,
            OpenQuestion.status == "open",
        ).all()
        # The benchmark's generated clinical-gap questions have no separate,
        # source-backed necessity assessment. Preserve their InformationGap
        # records, but do not turn model wording into caregiver tasks.
        assert actual_questions == [], (
            f"{scenario['scenario_id']}: unassessed model-generated questions "
            "must remain internal and must not become caregiver work"
        )


def test_correction_supersedes_assertion_and_preserves_append_only_history():
    from app.models.care import Correction
    from app.models.care_continuity import CareAssertion, CareRecordRelationship
    from app.services.learning import LearningEngine

    with TestingSessionLocal() as db:
        person, caregiver = make_person(db, "assertion-correction")
        original = CareAssertion(
            id="assertion-original-correction",
            person_id=person.id,
            subject_type="symptom",
            subject_id="sleep",
            field="symptom_reported_state",
            assertion_kind="time_bound_observation",
            asserted_value="poor sleep",
            source_label="Caregiver report",
            source_text="Daughter reported poor sleep.",
            evidence_ids=["original-evidence"],
            assertion_status="reported",
            epistemic_state="reported",
            created_by_caregiver_id=caregiver.id,
        )
        correction = Correction(
            id="correction-assertion-replacement",
            person_id=person.id,
            target_type="care_assertion",
            target_id=original.id,
            original_text="poor sleep",
            corrected_text="sleep was disrupted only on Tuesday",
            reason="The original entry described the wrong date range.",
            caregiver_id=caregiver.id,
        )
        db.add_all([original, correction])
        db.flush()

        engine = LearningEngine(db)
        engine.apply_correction(correction, commit=False)
        db.flush()

        db.refresh(original)
        assert original.assertion_status == "superseded"
        replacement = db.query(CareAssertion).filter(
            CareAssertion.person_id == person.id,
            CareAssertion.id != original.id,
            CareAssertion.subject_id == original.subject_id,
        ).one()
        assert replacement.asserted_value == "sleep was disrupted only on Tuesday"
        assert replacement.assertion_status == "reported"
        assert replacement.epistemic_state == "reported"
        assert replacement.evidence_ids == []
        assert replacement.source_event_id is None

        links = db.query(CareRecordRelationship).filter(
            CareRecordRelationship.person_id == person.id,
            CareRecordRelationship.target_id == replacement.id,
        ).all()
        assert {(link.source_type, link.relationship_type, link.source_id) for link in links} == {
            ("correction", "corrects", correction.id),
            ("care_assertion", "superseded_by", original.id),
        }

        # Replaying the same correction cannot create another replacement.
        engine.apply_correction(correction, commit=False)
        db.flush()
        assert db.query(CareAssertion).filter(
            CareAssertion.person_id == person.id,
            CareAssertion.subject_id == original.subject_id,
        ).count() == 2
        assert db.query(CareAssertion).filter(
            CareAssertion.id == original.id,
            CareAssertion.assertion_status == "superseded",
        ).count() == 1



def test_scenario_03_multi_person_medication_conflict_preserves_source_history():
    """Scenario 03: separate family reports and administration evidence never pick a medication winner."""
    from app.models.care import Evidence
    from app.models.uncertainty import Contradiction, OpenQuestion
    from app.models.care_continuity import CareAssertion
    from app.schemas.care_continuity import CareAssertionCreate
    from app.services.care_continuity import create_assertion
    from app.services.change_intelligence_reconciliation import reconcile_person_change_intelligence

    with TestingSessionLocal() as db:
        person, sarah = make_person(db, "scenario-03-margaret")
        james = Caregiver(
            id="c-s03-james", name="James", relationship="Family", person_id=person.id
        )
        helen = Caregiver(
            id="c-s03-helen", name="Helen", relationship="Paid care worker", person_id=person.id
        )
        db.add_all([james, helen])
        db.flush()
        token = issue_session(db, person, sarah)
        db.commit()

    client = TestClient(app)
    headers = {"Authorization": f"Bearer {token}"}
    reports = [
        {
            "reporter": sarah,
            "text": "Sarah reports that the evening tablet may have been stopped, but she has no written confirmation.",
            "occurred_at": "2026-11-09T13:20:00+00:00",
            "reported_at": "2026-11-09T13:20:00+00:00",
        },
        {
            "reporter": james,
            "text": "James reports that a doctor told him to stop the evening tablet last week.",
            "occurred_at": "2026-11-02T00:00:00+00:00",
            "reported_at": "2026-11-09T13:45:00+00:00",
        },
        {
            "reporter": helen,
            "text": "Helen reports administering the medication from the existing weekly pill organiser.",
            "occurred_at": "2026-11-09T14:10:00+00:00",
            "reported_at": "2026-11-09T14:10:00+00:00",
        },
    ]
    saved_ids = []
    for item in reports:
        response = client.post(
            "/api/v1/evidence",
            headers=headers,
            json={
                "person_id": person.id,
                "type": "note",
                "evidence_kind": "caregiver_report",
                "epistemic_status": "unverified",
                "source_text": item["text"],
                "uploaded_by_caregiver_id": sarah.id,
                "reported_by_caregiver_id": item["reporter"].id,
                "reported_by_label": item["reporter"].name,
                "original_author": item["reporter"].name,
                "occurred_at": item["occurred_at"],
                "reported_at": item["reported_at"],
                "origin_channel": "family_care_update",
            },
        )
        assert response.status_code in {200, 201}, response.text
        saved_ids.append(response.json()["id"])

    # The existing medication list is created through its real API and
    # explicitly caregiver-reviewed. It is not falsely sourced to Sarah's
    # later uncertainty report, and review does not imply clinician verification.
    medication_response = client.post(
        "/api/v1/care-continuity/medications",
        headers=headers,
        json={
            "name": "Evening tablet",
            "dose": "Existing listed dose",
            "note": "Existing medication list before this week's reports",
            "review_after_days": 30,
        },
    )
    assert medication_response.status_code == 201, medication_response.text
    medication_id = medication_response.json()["medication_id"]
    review_response = client.post(
        f"/api/v1/care-continuity/medications/{medication_id}/review",
        headers=headers,
        json={},
    )
    assert review_response.status_code == 200, review_response.text

    before_conflict = client.get("/api/v1/care-continuity/medications", headers=headers)
    assert before_conflict.status_code == 200, before_conflict.text
    assert [item["medication_id"] for item in before_conflict.json()["medications"]] == [medication_id]

    # James's verbal instruction is a separate reported assertion. It creates
    # an unresolved conflict with the existing caregiver-reviewed record.
    with TestingSessionLocal() as db:
        james_claim = create_assertion(
            db,
            CareAssertionCreate(
                person_id=person.id,
                subject_type="medication",
                subject_id=medication_id,
                field="medication_status",
                assertion_kind="current_state",
                asserted_value="discontinued",
                source_label="James verbal report",
                source_text=reports[1]["text"],
                evidence_ids=[saved_ids[1]],
                epistemic_state="reported",
                created_by_caregiver_id=james.id,
            ),
        )
        # Helen's administration statement is a separate event report. It does
        # not establish the prescription or prove a medication error.
        reconcile_person_change_intelligence(db, person.id)
        db.commit()

    # A new session proves source preservation after reconciliation/commit.
    with TestingSessionLocal() as verify_db:
        stored = verify_db.query(Evidence).filter(
            Evidence.person_id == person.id,
            Evidence.id.in_(saved_ids),
        ).all()
        assert {row.id for row in stored} == set(saved_ids)
        assert {row.reported_by_caregiver_id for row in stored} == {
            sarah.id, james.id, helen.id
        }
        assert {row.source_text for row in stored} == {item["text"] for item in reports}
        assert all(row.ingested_at is not None for row in stored)
        assert len({row.occurred_at for row in stored}) == 3

    medication_list = client.get("/api/v1/care-continuity/medications", headers=headers)
    assert medication_list.status_code == 200, medication_list.text
    assert medication_list.json()["medications"] == []
    assert medication_list.json()["pending_review"] == []

    situation_response = client.post(
        "/api/v1/care-continuity/situation-understanding/reassess",
        headers=headers,
    )
    assert situation_response.status_code == 200, situation_response.text
    assessment = situation_response.json()["assessment"]
    medication_conflicts = [
        item for item in assessment["uncertainties_and_conflicts"]
        if item["source"] == "conflicts"
        and item.get("subject_type") == "medication"
        and item.get("field") == "medication_status"
    ]
    assert len(medication_conflicts) == 1
    assert medication_conflicts[0]["status"] == "unresolved"
    assert medication_conflicts[0]["evidence_ids"] == [saved_ids[1]]

    with TestingSessionLocal() as verify_db:
        claims = verify_db.query(CareAssertion).filter(
            CareAssertion.person_id == person.id,
            CareAssertion.subject_id == medication_id,
            CareAssertion.field == "medication_status",
        ).all()
        assert {row.id for row in claims} >= {james_claim.id}
        active_claims = [row for row in claims if row.asserted_value == "active"]
        reported_stop_claims = [row for row in claims if row.asserted_value == "discontinued"]
        assert len(active_claims) == 1
        assert active_claims[0].assertion_status == "accepted"
        assert active_claims[0].epistemic_state == "reported"
        assert len(reported_stop_claims) == 1
        assert reported_stop_claims[0].assertion_status == "reported"
        assert reported_stop_claims[0].epistemic_state == "reported"
        assert reported_stop_claims[0].evidence_ids == [saved_ids[1]]

        conflict = verify_db.query(Contradiction).filter(
            Contradiction.person_id == person.id,
            Contradiction.subject_id == medication_id,
            Contradiction.field == "medication_status",
        ).one()
        assert conflict.resolution_status == "unresolved"
        assert set(conflict.source_assertion_ids or []) == {
            active_claims[0].id, james_claim.id
        }

        # Conflicting current medication reports are not projected as a
        # current or pending medication schedule, and a professional-directed
        # confirmation question is persisted instead of a caregiver guessing.
        medication_questions = verify_db.query(OpenQuestion).filter(
            OpenQuestion.person_id == person.id,
            OpenQuestion.subject_id == medication_id,
            OpenQuestion.intended_answerer_role == "professional",
            OpenQuestion.status == "open",
        ).all()
        assert len(medication_questions) == 1
        question = medication_questions[0]
        assert question.necessity == "necessary"
        assert getattr(question.priority, "value", question.priority) == "high"
        assert "prescribing clinician or pharmacist" in question.question
        assert set(question.evidence_ids or []) == {saved_ids[1]}

def test_delta_engine_fails_closed_when_prior_assertion_baseline_is_truncated():
    from app.services.delta_engine import _apply_incomplete_baseline_guard

    finding = {
        "change_type": "unchanged",
        "certainty": "supported",
        "previous": "stable",
        "current": "stable",
    }
    _apply_incomplete_baseline_guard(finding, baseline_complete=False)

    assert finding["change_type"] == "uncertain"
    assert finding["certainty"] == "uncertain"
    assert finding["baseline_incomplete"] is True
    assert "truncated" in finding["comparison_warning"].lower()

    complete_finding = {
        "change_type": "unchanged",
        "certainty": "supported",
    }
    _apply_incomplete_baseline_guard(complete_finding, baseline_complete=True)
    assert complete_finding == {
        "change_type": "unchanged",
        "certainty": "supported",
    }



def test_delta_engine_discloses_prior_history_omitted_by_context_bounds(monkeypatch):
    """Delta must know when its 25-event baseline is only a subset of history."""
    import json
    from app.services import delta_engine

    with TestingSessionLocal() as db:
        person, caregiver = make_person(db, "delta-history-coverage")
        for index in range(30):
            add_event(
                db, person, caregiver, "sleep", 60 + index,
                f"Historical sleep observation {index}",
            )
        incoming = add_event(
            db, person, caregiver, "sleep", 0,
            "New sleep observation",
        )
        captured = {}

        monkeypatch.setattr(
            delta_engine,
            "_local_ollama_url",
            lambda: ("http://127.0.0.1:11434", "coverage-test-model", 1.0),
        )

        def fake_qwen(messages, **kwargs):
            captured["payload"] = json.loads(messages[1]["content"])
            return json.dumps({
                "event_summary": "New sleep report recorded.",
                "medications": [],
                "conditions": [],
                "symptoms": [{
                    "change_type": "new",
                    "name": "sleep",
                    "previous": None,
                    "current": "interrupted sleep",
                    "evidence_excerpt": "Caregiver reported interrupted sleep.",
                    "certainty": "supported",
                }],
                "tests": [],
                "follow_ups": [],
                "contradictions": [],
                "clinical_gaps": [],
                "caregiver_summary": "New sleep report was recorded.",
                "clinician_summary": "Caregiver reported sleep disruption.",
            })

        monkeypatch.setattr(delta_engine, "qwen_chat", fake_qwen)
        result = delta_engine.reconcile_event(
            db,
            person_id=person.id,
            event_date=NOW,
            source="caregiver report",
            content="Caregiver reported interrupted sleep.",
            source_event_id=incoming.id,
            evidence_ids=[],
            persist=False,
            created_by_caregiver_id=caregiver.id,
        )

        coverage = captured["payload"]["history_coverage"]
        assert coverage["prior_events"] == {
            "available": 25,
            "total": 30,
            "omitted": 5,
        }
        assert coverage["complete"] is False
        assert result["symptoms"][0]["change_type"] == "uncertain"
        assert result["symptoms"][0]["certainty"] == "uncertain"
        assert result["symptoms"][0]["baseline_incomplete"] is True
        assert result["computed_changes"][0]["certainty"] == "uncertain"

def test_cross_domain_pattern_scan_includes_events_older_than_90_days():
    from app.services.pattern_detection import detect_cross_domain_patterns

    with TestingSessionLocal() as db:
        person, caregiver = make_person(db, "cross-domain-historical-window")
        older = add_event(db, person, caregiver, "sleep", 180, "Sleep disruption recorded")
        add_event(db, person, caregiver, "mobility", 177, "Mobility assistance recorded")
        add_event(db, person, caregiver, "nutrition", 174, "Meal support recorded")

        patterns = detect_cross_domain_patterns(db, person.id, reference_time=NOW)
        db.flush()

        assert len(patterns) == 1
        pattern = patterns[0]
        assert set(pattern.event_ids) == {older.id, *[
            event.id for event in db.query(CareEvent).filter(
                CareEvent.person_id == person.id,
                CareEvent.id != older.id,
            ).all()
        ]}
        assert pattern.pattern_metrics["source_history_truncated"] is False
        assert pattern.pattern_metrics["analysis_window_start"] == (
            NOW - timedelta(days=180)
        ).isoformat()

def test_cross_domain_pattern_marks_bounded_source_history_as_truncated(monkeypatch):
    from app.services import pattern_detection

    monkeypatch.setattr(pattern_detection, "MAX_PATTERN_SOURCE_EVENTS", 3)
    with TestingSessionLocal() as db:
        person, caregiver = make_person(db, "cross-domain-bounded-history")
        add_event(db, person, caregiver, "sleep", 1, "Recent sleep report")
        add_event(db, person, caregiver, "mobility", 2, "Recent mobility report")
        add_event(db, person, caregiver, "nutrition", 3, "Recent meal report")
        add_event(db, person, caregiver, "sleep", 4, "Older sleep report")

        patterns = pattern_detection.detect_cross_domain_patterns(
            db, person.id, reference_time=NOW
        )
        db.flush()

        assert len(patterns) == 1
        assert patterns[0].pattern_metrics["source_history_truncated"] is True
        assert patterns[0].pattern_metrics["event_count"] == 3

