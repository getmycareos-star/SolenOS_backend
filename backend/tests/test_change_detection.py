from datetime import datetime, timedelta, timezone
import pytest
from sqlalchemy.exc import IntegrityError
import hashlib
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, SessionLocal as DefaultSessionLocal
import app.main as main_module
from app.main import app
from app.models.care import Person, Caregiver, CareEvent, Evidence
from app.models.anonymous_session import AnonymousSession
from app.models.care_continuity import CareAssertion, EvidenceObservation
from app.models.change import Baseline, Change, Pattern, ChangeConfirmationEvent
from app.models.observation_coverage import ObservationCoverage

SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine, expire_on_commit=False)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


def setup_db():
    Base.metadata.create_all(bind=engine)
    main_module.SessionLocal = TestingSessionLocal


def issue_scoped_session(db, person, caregiver):
    token = f"change-api-token-{person.id}"
    db.add(
        AnonymousSession(
            id=f"session-{person.id}",
            person_id=person.id,
            caregiver_id=caregiver.id,
            token_hash=hashlib.sha256(token.encode("utf-8")).hexdigest(),
            expires_at=datetime.now(timezone.utc) + timedelta(days=1),
        )
    )
    db.commit()
    return token


def teardown_db():
    app.dependency_overrides.clear()
    main_module.SessionLocal = DefaultSessionLocal
    Base.metadata.drop_all(bind=engine)


def test_baseline_selection_uses_longest_stable_window():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db

        db = TestingSessionLocal()
        person = Person(id="person-1", name="Test Person")
        caregiver = Caregiver(
            id="caregiver-1",
            name="Test Caregiver",
            email="test@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()
        now = datetime.now(timezone.utc)
        for i in range(5):
            event = CareEvent(
                person_id=person.id,
                event_type="fall",
                status="recorded",
                occurred_at=now - timedelta(days=200 + i * 30),
                title=f"Fall {i+1}",
                created_by_caregiver_id=caregiver.id,
            )
            db.add(event)
        db.commit()
        from app.services.baseline import build_baseline
        baseline = build_baseline(db, person.id, "fall", None, now)
        assert baseline is not None
        assert baseline.event_count == 5
        assert baseline.confidence.value == "low"
        assert baseline.is_stable is False
        assert baseline.observation_density is None
        assert baseline.documentation_bias_flag is True
        assert baseline.coverage_status == "unknown"
        assert baseline.analysis_version == "longitudinal-baseline-v2"
        assert baseline.history_completeness == "complete"
        db.close()
    finally:
        teardown_db()


def test_change_detection_onset():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db

        db = TestingSessionLocal()
        person = Person(id="person-2", name="Test Person 2")
        caregiver = Caregiver(
            id="caregiver-2",
            name="Test Caregiver 2",
            email="test2@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()
        now = datetime.now(timezone.utc)
        for i in range(3):
            event = CareEvent(
                person_id=person.id,
                event_type="fall",
                status="recorded",
                occurred_at=now - timedelta(days=7 + i * 7),
                title=f"Fall {i+1}",
                created_by_caregiver_id=caregiver.id,
            )
            db.add(event)
        db.commit()
        from app.services.change_detection import detect_event_changes
        changes = detect_event_changes(db, person.id, "fall", None, now)
        assert len(changes) == 1
        assert changes[0].change_type.value == "emergence"
        assert "No comparable prior events" in changes[0].previous_state
        # No explicit coverage record exists for the baseline window, so a
        # zero prior count would incorrectly turn missing observation into absence.
        assert changes[0].previous_count is None
        assert changes[0].baseline_coverage_status == "unknown"
        assert changes[0].current_coverage_status == "unknown"
        assert changes[0].current_count == 3
        db.close()
    finally:
        teardown_db()


def test_pattern_detection_recurrence_uses_persisted_events_and_is_idempotent():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db

        db = TestingSessionLocal()
        person = Person(id="person-3", name="Test Person 3")
        caregiver = Caregiver(
            id="caregiver-3",
            name="Test Caregiver 3",
            email="test3@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()
        now = datetime.now(timezone.utc)
        from app.core.change_enums import ChangeType, ChangeDirection, PatternType
        from app.services.pattern_detection import detect_patterns
        event_ids = []
        for i in range(2):
            event = CareEvent(
                person_id=person.id,
                event_type="fall",
                status="recorded",
                occurred_at=now - timedelta(days=30 + i * 30),
                title=f"Persisted fall event {i + 1}",
                created_by_caregiver_id=caregiver.id,
            )
            db.add(event)
            db.flush()
            event_ids.append(event.id)
            db.add(
                Change(
                    person_id=person.id,
                    subject_type="fall",
                    subject_id="",
                    change_type=ChangeType.INCREASE,
                    change_direction=ChangeDirection.INCREASING,
                    previous_state="Recorded rate before comparison",
                    current_state="Recorded rate after comparison",
                    previous_count=1,
                    current_count=2 + i,
                    confidence=None,
                    evidence_ids=[event.id],
                    created_at=now - timedelta(days=30 + i * 30),
                )
            )
        db.commit()

        patterns = detect_patterns(db, person.id, "fall", None, now)
        db.commit()
        assert len(patterns) == 1
        pattern = patterns[0]
        assert pattern.pattern_type.value == "recurrence"
        assert set(pattern.event_ids) == set(event_ids)
        assert pattern.evidence_count == 2
        assert pattern.confidence is None
        assert pattern.rate_per_period is None
        assert any(
            "observation coverage" in note.lower() and "withheld" in note.lower()
            for note in (pattern.alternative_interpretations or [])
        )

        repeated = detect_patterns(db, person.id, "fall", None, now)
        db.commit()
        assert [item.id for item in repeated] == [pattern.id]
        assert (
            db.query(Pattern)
            .filter(
                Pattern.person_id == person.id,
                Pattern.subject_type == "fall",
                Pattern.is_active.is_(True),
            )
            .count()
            == 1
        )

        # New persisted evidence must supersede the prior pattern rather than
        # creating a second active pattern for the same subject.
        new_event = CareEvent(
            person_id=person.id,
            event_type="fall",
            status="recorded",
            occurred_at=now - timedelta(days=1),
            title="Persisted fall event 3",
            created_by_caregiver_id=caregiver.id,
        )
        db.add(new_event)
        db.commit()
        updated_patterns = detect_patterns(db, person.id, "fall", None, now)
        db.commit()
        active_recurrence = [
            item for item in updated_patterns
            if item.pattern_type.value == "recurrence" and item.is_active
        ]
        assert len(active_recurrence) == 1
        assert active_recurrence[0].id != pattern.id
        old_pattern = db.query(Pattern).filter(Pattern.id == pattern.id).one()
        assert old_pattern.is_active is False
        assert old_pattern.superseded_by_pattern_id == active_recurrence[0].id

        duplicate_active = Pattern(
            person_id=person.id,
            subject_type="fall",
            subject_id="",
            pattern_type=PatternType.RECURRENCE,
            pattern_strength=active_recurrence[0].pattern_strength,
            event_ids=list(active_recurrence[0].event_ids),
            change_ids=list(active_recurrence[0].change_ids or []),
            temporal_start=active_recurrence[0].temporal_start,
            temporal_end=active_recurrence[0].temporal_end,
            evidence_count=active_recurrence[0].evidence_count,
            is_active=True,
        )
        db.add(duplicate_active)
        with pytest.raises(IntegrityError):
            db.flush()
        db.rollback()

        assert (
            db.query(Pattern)
            .filter(
                Pattern.person_id == person.id,
                Pattern.subject_type == "fall",
                Pattern.is_active.is_(True),
                Pattern.pattern_type == PatternType.RECURRENCE,
            )
            .count()
            == 1
        )
        db.close()
    finally:
        teardown_db()


def test_situation_formation_from_changes():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db

        db = TestingSessionLocal()
        person = Person(id="person-4", name="Test Person 4")
        caregiver = Caregiver(
            id="caregiver-4",
            name="Test Caregiver 4",
            email="test4@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()
        now = datetime.now(timezone.utc)
        from app.services.situation_formation import detect_situations
        for i in range(3):
            event = CareEvent(
                person_id=person.id,
                event_type="fall",
                status="recorded",
                occurred_at=now - timedelta(days=7 + i * 7),
                title=f"Fall {i+1}",
                created_by_caregiver_id=caregiver.id,
            )
            db.add(event)
        db.commit()
        situations = detect_situations(db, person.id, now)
        assert len(situations) >= 1
        db.close()
    finally:
        teardown_db()


def test_situation_update_on_new_event():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db

        db = TestingSessionLocal()
        person = Person(id="person-5", name="Test Person 5")
        caregiver = Caregiver(
            id="caregiver-5",
            name="Test Caregiver 5",
            email="test5@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()
        now = datetime.now(timezone.utc)
        from app.services.situation_formation import detect_situations, update_situations
        from app.services.change_detection import detect_event_changes
        for i in range(3):
            event = CareEvent(
                person_id=person.id,
                event_type="fall",
                status="recorded",
                occurred_at=now - timedelta(days=7 + i * 7),
                title=f"Fall {i+1}",
                created_by_caregiver_id=caregiver.id,
            )
            db.add(event)
        db.commit()
        detect_event_changes(db, person.id, "fall", None, now)
        situations = detect_situations(db, person.id, now)
        assert len(situations) >= 1
        new_event = CareEvent(
            person_id=person.id,
            event_type="fall",
            status="recorded",
            occurred_at=now,
            title="Fall 4",
            created_by_caregiver_id=caregiver.id,
        )
        db.add(new_event)
        db.commit()
        updated = update_situations(db, person.id, new_event, now)
        assert len(updated) >= 1
        assert new_event.id in (updated[0].signal_ids or [])
        db.close()
    finally:
        teardown_db()


def test_duplicate_events_not_counted_as_multiple_falls():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db

        db = TestingSessionLocal()
        person = Person(id="person-6", name="Test Person 6")
        caregiver = Caregiver(
            id="caregiver-6",
            name="Test Caregiver 6",
            email="test6@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()
        now = datetime.now(timezone.utc)
        event_time = now - timedelta(days=3)
        event = CareEvent(
            person_id=person.id,
            event_type="fall",
            status="recorded",
            occurred_at=event_time,
            title="Fall",
            created_by_caregiver_id=caregiver.id,
            evidence_ids=["evidence-1", "evidence-2"],
        )
        db.add(event)
        db.flush()
        # A second persisted row linked to exactly the same source evidence is
        # duplicate documentation, not an independent occurrence.
        db.add(CareEvent(
            person_id=person.id,
            event_type="fall",
            status="recorded",
            occurred_at=event_time,
            title="Fall duplicate record",
            created_by_caregiver_id=caregiver.id,
            evidence_ids=["evidence-1", "evidence-2"],
        ))
        db.commit()
        from app.services.baseline import get_longitudinal_event_counts
        counts = get_longitudinal_event_counts(db, person.id, "fall", None, now)
        assert counts["total_events"] == 1
        db.close()
    finally:
        teardown_db()


def test_insufficient_baseline_low_confidence():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db

        db = TestingSessionLocal()
        person = Person(id="person-7", name="Test Person 7")
        caregiver = Caregiver(
            id="caregiver-7",
            name="Test Caregiver 7",
            email="test7@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()
        now = datetime.now(timezone.utc)
        event = CareEvent(
            person_id=person.id,
            event_type="fall",
            status="recorded",
            occurred_at=now,
            title="Fall",
            created_by_caregiver_id=caregiver.id,
        )
        db.add(event)
        db.commit()
        from app.services.baseline import build_baseline
        baseline = build_baseline(db, person.id, "fall", None, now)
        assert baseline is not None
        assert baseline.confidence.value == "low"
        db.close()
    finally:
        teardown_db()


def test_api_detect_changes():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db

        db = TestingSessionLocal()
        person = Person(id="person-8", name="Test Person 8")
        caregiver = Caregiver(
            id="caregiver-8",
            name="Test Caregiver 8",
            email="test8@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()
        now = datetime.now(timezone.utc)
        for i in range(3):
            event = CareEvent(
                person_id=person.id,
                event_type="fall",
                status="recorded",
                occurred_at=now - timedelta(days=7 + i * 7),
                title=f"Fall {i+1}",
                created_by_caregiver_id=caregiver.id,
            )
            db.add(event)
        db.commit()
        person_id = person.id
        token = issue_scoped_session(db, person, caregiver)
        db.close()

        client = TestClient(app)
        response = client.get(
            "/api/v1/change/changes/detect",
            params={"person_id": person_id, "event_type": "fall"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 1
    finally:
        teardown_db()



def test_reassessment_persists_recurrence_pattern_from_real_care_events():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db

        db = TestingSessionLocal()
        person = Person(id="person-pattern-api", name="Pattern API Person")
        caregiver = Caregiver(
            id="caregiver-pattern-api",
            name="Pattern API Caregiver",
            email="pattern-api@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()
        now = datetime.now(timezone.utc)
        for index, days_ago in enumerate((28, 14, 2), start=1):
            db.add(
                CareEvent(
                    person_id=person.id,
                    event_type="fall",
                    status="recorded",
                    occurred_at=now - timedelta(days=days_ago),
                    title=f"Persisted fall observation {index}",
                    created_by_caregiver_id=caregiver.id,
                )
            )
        db.commit()
        token = issue_scoped_session(db, person, caregiver)
        person_id = person.id
        db.close()

        client = TestClient(app)
        response = client.post(
            "/api/v1/change/changes/reassess",
            params={"person_id": person_id},
            headers={"Authorization": f"Bearer {token}"},
            json={},
        )
        assert response.status_code == 200, response.text

        persisted = client.get(
            f"/api/v1/change/patterns/{person_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert persisted.status_code == 200, persisted.text
        recurrence = [
            item for item in persisted.json()
            if item["pattern_type"] == "recurrence" and item["is_active"]
        ]
        assert len(recurrence) == 1
        assert recurrence[0]["evidence_count"] == 3
        assert recurrence[0]["confidence"] is None
        recurrence_limitations = " ".join(
            recurrence[0].get("alternative_interpretations") or []
        ).lower()
        assert "observation coverage across the event span is not explicitly complete" in recurrence_limitations
        assert "no complete observation-coverage record" in recurrence_limitations
    finally:
        teardown_db()


def test_api_detect_situations():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db

        db = TestingSessionLocal()
        person = Person(id="person-9", name="Test Person 9")
        caregiver = Caregiver(
            id="caregiver-9",
            name="Test Caregiver 9",
            email="test9@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()
        now = datetime.now(timezone.utc)
        for i in range(3):
            event = CareEvent(
                person_id=person.id,
                event_type="fall",
                status="recorded",
                occurred_at=now - timedelta(days=7 + i * 7),
                title=f"Fall {i+1}",
                created_by_caregiver_id=caregiver.id,
            )
            db.add(event)
        db.commit()
        person_id = person.id
        token = issue_scoped_session(db, person, caregiver)
        db.close()

        client = TestClient(app)
        response = client.get(
            "/api/v1/change/situations/detect",
            params={"person_id": person_id},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 1
    finally:
        teardown_db()

def test_change_detection_is_idempotent_for_unchanged_persisted_evidence():
    setup_db()
    try:
        db = TestingSessionLocal()
        person = Person(id="person-idempotency", name="Persisted Person")
        caregiver = Caregiver(
            id="caregiver-idempotency",
            name="Persisted Caregiver",
            email="idempotency@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()

        now = datetime.now(timezone.utc)
        for index in range(3):
            db.add(
                CareEvent(
                    person_id=person.id,
                    event_type="fall",
                    status="recorded",
                    occurred_at=now - timedelta(days=7 + index * 7),
                    title=f"Persisted event {index + 1}",
                    created_by_caregiver_id=caregiver.id,
                )
            )
        db.commit()

        from app.services.change_detection import detect_event_changes

        first = detect_event_changes(db, person.id, "fall", None, now)
        db.commit()
        first_ids = [change.id for change in first]
        assert len(first_ids) == 1

        second = detect_event_changes(db, person.id, "fall", None, now)
        db.commit()
        second_ids = [change.id for change in second]
        stored = (
            db.query(Change)
            .filter(
                Change.person_id == person.id,
                Change.subject_type == "fall",
                Change.is_active.is_(True),
            )
            .all()
        )

        assert second_ids == first_ids
        assert len(stored) == 1
        assert (
            db.query(Baseline)
            .filter(
                Baseline.person_id == person.id,
                Baseline.subject_type == "fall",
                Baseline.is_active.is_(True),
            )
            .count()
            == 1
        )
        assert set(stored[0].evidence_ids) == {
            event.id
            for event in db.query(CareEvent).filter(CareEvent.person_id == person.id).all()
        }
        db.close()
    finally:
        teardown_db()

def test_higher_normalized_rate_is_detected_even_when_raw_counts_are_lower():
    setup_db()
    try:
        db = TestingSessionLocal()
        person = Person(id="person-normalized-rate", name="Rate comparison recipient")
        caregiver = Caregiver(
            id="caregiver-normalized-rate",
            name="Rate comparison caregiver",
            email="rate-comparison@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()

        now = datetime.now(timezone.utc)
        for index in range(6):
            db.add(
                CareEvent(
                    person_id=person.id,
                    event_type="mobility_observation",
                    status="recorded",
                    occurred_at=now - timedelta(days=70 + index * 20),
                    title=f"Historical mobility event {index + 1}",
                    created_by_caregiver_id=caregiver.id,
                )
            )
        for index in range(3):
            db.add(
                CareEvent(
                    person_id=person.id,
                    event_type="mobility_observation",
                    status="recorded",
                    occurred_at=now - timedelta(days=7 + index * 10),
                    title=f"Recent mobility event {index + 1}",
                    created_by_caregiver_id=caregiver.id,
                )
            )
        db.commit()

        from app.services.change_detection import detect_event_changes

        changes_without_coverage = detect_event_changes(db, person.id, "mobility_observation", None, now)
        assert not any(change.change_type.value == "increase" for change in changes_without_coverage), (
            "A higher recorded rate is not an occurrence-rate increase without complete coverage."
        )

        from app.services.baseline import build_baseline
        baseline = build_baseline(db, person.id, "mobility_observation", None, now)
        assert baseline is not None
        db.add_all([
            ObservationCoverage(
                id="mobility-baseline-coverage", person_id=person.id,
                subject_type="mobility_observation",
                window_start=baseline.window_start, window_end=baseline.window_end,
                coverage_status="covered", source_evidence_ids=[],
                rationale="The domain was explicitly observed throughout the baseline window.",
                recorded_by_caregiver_id=caregiver.id,
            ),
            ObservationCoverage(
                id="mobility-current-coverage", person_id=person.id,
                subject_type="mobility_observation",
                window_start=now - timedelta(days=42), window_end=now,
                coverage_status="covered", source_evidence_ids=[],
                rationale="The domain was explicitly observed throughout the current window.",
                recorded_by_caregiver_id=caregiver.id,
            ),
        ])
        db.commit()

        changes = detect_event_changes(db, person.id, "mobility_observation", None, now)
        assert any(change.change_type.value == "increase" for change in changes)
        increase = next(change for change in changes if change.change_type.value == "increase")
        assert increase.previous_count == 6
        assert increase.current_count == 3
        assert "per 30 days" in increase.previous_state
        db.close()
    finally:
        teardown_db()


def test_no_recent_events_do_not_create_a_cessation_claim_without_coverage():
    setup_db()
    try:
        db = TestingSessionLocal()
        person = Person(id="person-no-coverage", name="Observation coverage recipient")
        caregiver = Caregiver(
            id="caregiver-no-coverage",
            name="Observation coverage caregiver",
            email="coverage@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()

        now = datetime.now(timezone.utc)
        for index in range(3):
            db.add(
                CareEvent(
                    person_id=person.id,
                    event_type="routine_observation",
                    status="recorded",
                    occurred_at=now - timedelta(days=70 + index * 20),
                    title=f"Historical routine observation {index + 1}",
                    created_by_caregiver_id=caregiver.id,
                )
            )
        db.commit()

        from app.services.change_detection import detect_event_changes

        changes = detect_event_changes(db, person.id, "routine_observation", None, now)
        assert all(change.change_type.value != "cessation" for change in changes)
        db.close()
    finally:
        teardown_db()

def test_new_evidence_supersedes_prior_active_change_without_erasing_history():
    setup_db()
    try:
        db = TestingSessionLocal()
        person = Person(id="person-supersession", name="Supersession recipient")
        caregiver = Caregiver(
            id="caregiver-supersession",
            name="Supersession caregiver",
            email="supersession@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()
        now = datetime.now(timezone.utc)

        for index in range(3):
            db.add(
                CareEvent(
                    person_id=person.id,
                    event_type="mobility_observation",
                    status="recorded",
                    occurred_at=now - timedelta(days=7 + index * 7),
                    title=f"Recorded mobility observation {index + 1}",
                    created_by_caregiver_id=caregiver.id,
                )
            )
        db.commit()

        from app.services.change_detection import detect_event_changes

        initial = detect_event_changes(db, person.id, "mobility_observation", None, now)
        db.commit()
        assert initial
        old_ids = {change.id for change in initial}

        db.add(
            CareEvent(
                person_id=person.id,
                event_type="mobility_observation",
                status="recorded",
                occurred_at=now - timedelta(days=1),
                title="Additional recorded mobility observation",
                created_by_caregiver_id=caregiver.id,
            )
        )
        db.commit()

        updated = detect_event_changes(db, person.id, "mobility_observation", None, now)
        db.commit()
        assert updated
        active = (
            db.query(Change)
            .filter(
                Change.person_id == person.id,
                Change.subject_type == "mobility_observation",
                Change.is_active.is_(True),
            )
            .all()
        )
        historical = (
            db.query(Change)
            .filter(
                Change.person_id == person.id,
                Change.id.in_(old_ids),
            )
            .all()
        )

        assert len(active) == 1
        assert active[0].id not in old_ids
        assert historical
        assert all(not item.is_active for item in historical)
        assert all(item.superseded_by_change_id == active[0].id for item in historical)
        assert len(set(active[0].evidence_ids)) == len(active[0].evidence_ids)
        db.close()
    finally:
        teardown_db()

def test_change_detail_returns_persisted_source_evidence_and_blocks_cross_person_access():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db
        db = TestingSessionLocal()
        person = Person(id="person-change-detail", name="Change detail recipient")
        caregiver = Caregiver(
            id="caregiver-change-detail",
            name="Change detail caregiver",
            email="change-detail@example.com",
            relationship="Family",
            person_id=person.id,
        )
        other_person = Person(id="person-change-detail-other", name="Other recipient")
        other_caregiver = Caregiver(
            id="caregiver-change-detail-other",
            name="Other caregiver",
            email="change-detail-other@example.com",
            relationship="Family",
            person_id=other_person.id,
        )
        db.add_all([person, caregiver, other_person, other_caregiver])
        db.commit()
        now = datetime.now(timezone.utc)
        for index in range(3):
            db.add(
                CareEvent(
                    person_id=person.id,
                    event_type="fall",
                    status="recorded",
                    occurred_at=now - timedelta(days=7 + index * 7),
                    title=f"Persisted fall observation {index + 1}",
                    description="Recorded source observation.",
                    time_provenance="caregiver_reported",
                    created_by_caregiver_id=caregiver.id,
                )
            )
        db.commit()
        from app.services.change_detection import detect_event_changes
        changes = detect_event_changes(db, person.id, "fall", None, now)
        db.commit()
        assert changes
        change_id = changes[0].id
        person_id = person.id
        token = issue_scoped_session(db, person, caregiver)
        other_token = issue_scoped_session(db, other_person, other_caregiver)
        db.close()

        client = TestClient(app)
        response = client.get(
            f"/api/v1/change/changes/{person_id}/{change_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["change"]["id"] == change_id
        assert payload["change"]["person_id"] == person_id
        assert payload["evidence"]
        assert all(item["id"] in payload["change"]["evidence_ids"] for item in payload["evidence"])
        assert all(item["title"].startswith("Persisted fall observation") for item in payload["evidence"])

        forbidden = client.get(
            f"/api/v1/change/changes/{person_id}/{change_id}",
            headers={"Authorization": f"Bearer {other_token}"},
        )
        assert forbidden.status_code == 403
    finally:
        teardown_db()

def test_change_confirmation_lifecycle_persists_actor_reason_and_transition_history():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db
        db = TestingSessionLocal()
        person = Person(id="person-confirmation", name="Confirmation recipient")
        caregiver = Caregiver(
            id="caregiver-confirmation",
            name="Confirmation caregiver",
            email="confirmation@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()
        now = datetime.now(timezone.utc)
        for index in range(3):
            db.add(
                CareEvent(
                    person_id=person.id,
                    event_type="fall",
                    status="recorded",
                    occurred_at=now - timedelta(days=7 + index * 7),
                    title=f"Persisted fall evidence {index + 1}",
                    created_by_caregiver_id=caregiver.id,
                )
            )
        db.commit()
        from app.services.change_detection import detect_event_changes
        changes = detect_event_changes(db, person.id, "fall", None, now)
        db.commit()
        assert changes
        person_id = person.id
        caregiver_id = caregiver.id
        change_id = changes[0].id
        evidence_id = changes[0].evidence_ids[0]
        token = issue_scoped_session(db, person, caregiver)
        db.close()

        client = TestClient(app)
        url = f"/api/v1/change/changes/{person_id}/{change_id}/confirmation"
        missing_evidence = client.post(
            url,
            json={"status": "confirmed", "reason": "Caregiver reviewed the linked observations.", "evidence_ids": []},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert missing_evidence.status_code == 422

        missing_rationale = client.post(
            url,
            json={"status": "confirmed", "evidence_ids": [evidence_id]},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert missing_rationale.status_code == 422

        response = client.post(
            url,
            json={"status": "confirmed", "reason": "Caregiver reviewed the linked observations.", "evidence_ids": [evidence_id]},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["previous_status"] == "detected"
        assert payload["new_status"] == "confirmed"
        assert payload["actor_caregiver_id"] == caregiver_id
        assert payload["change"]["confirmation_status"] == "confirmed"

        db = TestingSessionLocal()
        saved = db.query(Change).filter(Change.id == change_id, Change.person_id == person_id).one()
        transitions = db.query(ChangeConfirmationEvent).filter(ChangeConfirmationEvent.change_id == change_id).all()
        assert saved.confirmation_status == "confirmed"
        assert len(transitions) == 1
        assert transitions[0].actor_caregiver_id == caregiver_id
        assert transitions[0].reason == "Caregiver reviewed the linked observations."
        assert transitions[0].evidence_ids == [evidence_id]
        db.close()

        invalid_repeat = client.post(
            url,
            json={"status": "confirmed", "reason": "Repeated confirmation.", "evidence_ids": []},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert invalid_repeat.status_code == 409

        missing_reason = client.post(
            url,
            json={"status": "rejected", "evidence_ids": []},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert missing_reason.status_code == 422
    finally:
        teardown_db()

def test_structured_assertion_transition_is_persisted_idempotently_and_returns_source_assertions():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db
        db = TestingSessionLocal()
        person = Person(id="person-assertion-transition", name="Assertion transition recipient")
        caregiver = Caregiver(
            id="caregiver-assertion-transition",
            name="Assertion transition caregiver",
            email="assertion-transition@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()
        now = datetime.now(timezone.utc)
        observations = [
            ("independent", 10),
            ("needs assistance", 3),
            ("needs assistance", 1),
        ]
        assertion_ids = []
        for index, (value, days_ago) in enumerate(observations):
            event = CareEvent(
                person_id=person.id,
                event_type="functional_observation",
                status="recorded",
                occurred_at=now - timedelta(days=days_ago),
                title=f"Structured functional observation {index + 1}",
                created_by_caregiver_id=caregiver.id,
            )
            db.add(event)
            db.flush()
            assertion = CareAssertion(
                person_id=person.id,
                subject_type="functional",
                subject_id="daily_activity",
                field="assistance_level",
                assertion_kind="time_bound_observation",
                asserted_value=value,
                source_event_id=event.id,
                source_label="caregiver_record",
                source_text=None,
                evidence_ids=[],
                assertion_status="reported",
                observed_at=now - timedelta(days=days_ago),
                created_by_caregiver_id=caregiver.id,
            )
            db.add(assertion)
            db.flush()
            assertion_ids.append(assertion.id)
        db.commit()

        from app.services.change_detection import detect_assertion_changes
        first = detect_assertion_changes(db, person.id, now)
        db.commit()
        assert len(first) == 1
        change_id = first[0].id
        assert first[0].previous_state == "independent"
        assert first[0].current_state == "needs assistance"
        assert first[0].change_direction.value == "unknown"
        assert first[0].significance_verdict.value == "insufficient_evidence"
        assert first[0].persistence_days is None
        assert "not treated as continuous persistence" in first[0].explanation
        assert first[0].is_persistent is None
        assert set(first[0].evidence_ids) == set(assertion_ids)

        second = detect_assertion_changes(db, person.id, now)
        db.commit()
        assert [item.id for item in second] == [change_id]
        token = issue_scoped_session(db, person, caregiver)
        person_id = person.id
        db.close()

        response = TestClient(app).get(
            f"/api/v1/change/changes/{person_id}/{change_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert {item["id"] for item in payload["assertions"]} == set(assertion_ids)
        assert payload["change"]["confirmation_status"] == "detected"
    finally:
        teardown_db()



def test_recurrence_rate_requires_complete_explicit_observation_coverage():
    setup_db()
    try:
        db = TestingSessionLocal()
        person = Person(id="person-coverage-rate", name="Coverage rate test")
        caregiver = Caregiver(
            id="caregiver-coverage-rate",
            name="Coverage Rate Caregiver",
            email="coverage-rate@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()
        now = datetime.now(timezone.utc)
        earlier = now - timedelta(days=60)
        later = now - timedelta(days=30)
        for i, occurred_at in enumerate((earlier, later)):
            db.add(CareEvent(
                person_id=person.id,
                event_type="fall",
                status="recorded",
                occurred_at=occurred_at,
                title=f"Covered recurrence event {i + 1}",
                created_by_caregiver_id=caregiver.id,
            ))
        db.add(ObservationCoverage(
            id="coverage-rate-window",
            person_id=person.id,
            subject_type="fall",
            window_start=now - timedelta(days=90),
            window_end=now,
            coverage_status="covered",
            source_evidence_ids=[],
            rationale="The caregiver explicitly recorded observation coverage for this interval.",
            recorded_by_caregiver_id=caregiver.id,
        ))
        db.commit()

        from app.services.pattern_detection import detect_patterns
        patterns = detect_patterns(db, person.id, "fall", None, now)
        db.commit()
        recurrence = next(item for item in patterns if item.pattern_type.value == "recurrence")
        assert recurrence.rate_per_period is not None
        assert recurrence.rate_per_period == pytest.approx(2.0 / 3.0)
        assert not any(
            "rate is withheld" in note.lower()
            for note in (recurrence.alternative_interpretations or [])
        )
        db.close()
    finally:
        teardown_db()


def test_trend_and_instability_patterns_require_persisted_coverage_provenance():
    setup_db()
    try:
        db = TestingSessionLocal()
        person = Person(id="person-pattern-coverage", name="Pattern coverage test")
        caregiver = Caregiver(
            id="caregiver-pattern-coverage",
            name="Pattern Coverage Caregiver",
            email="pattern-coverage@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()
        now = datetime.now(timezone.utc)
        from app.core.change_enums import ChangeType, ChangeDirection, PatternType
        from app.services.pattern_detection import detect_patterns
        directions = (
            ChangeDirection.INCREASING,
            ChangeDirection.DECREASING,
            ChangeDirection.INCREASING,
        )
        for i, direction in enumerate(directions):
            event_time = now - timedelta(days=30 - i * 10)
            event = CareEvent(
                id=f"pattern-coverage-event-{i}",
                person_id=person.id,
                event_type="mobility",
                status="recorded",
                occurred_at=event_time,
                title=f"Recorded mobility event {i}",
                created_by_caregiver_id=caregiver.id,
            )
            db.add(event)
            db.flush()
            db.add(Change(
                id=f"pattern-coverage-change-{i}",
                person_id=person.id,
                subject_type="mobility",
                subject_id="",
                change_type=ChangeType.INCREASE,
                change_direction=direction,
                previous_state="Prior recorded state",
                current_state="Current recorded state",
                magnitude=10.0,
                previous_count=1,
                current_count=2,
                comparison_window_start=event_time - timedelta(days=7),
                comparison_window_end=event_time,
                confidence=None,
                evidence_ids=[event.id],
                baseline_coverage_status=None,
                current_coverage_status=None,
                created_at=event_time,
            ))
        db.commit()

        patterns = detect_patterns(db, person.id, "mobility", None, now)
        db.commit()
        pattern_types = {item.pattern_type for item in patterns}
        assert PatternType.TREND not in pattern_types
        assert PatternType.INSTABILITY not in pattern_types
        db.close()
    finally:
        teardown_db()


def test_coverage_assessment_api_rejects_naive_query_timestamps():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db
        db = TestingSessionLocal()
        person = Person(id="coverage-api-person", name="Coverage API test")
        caregiver = Caregiver(
            id="coverage-api-caregiver",
            name="Coverage API Caregiver",
            email="coverage-api@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()
        token = issue_scoped_session(db, person, caregiver)
        person_id = person.id
        db.close()

        client = TestClient(app)
        response = client.get(
            f"/api/v1/change/coverage/{person_id}",
            params={
                "subject_type": "mobility",
                "window_start": "2026-10-01T00:00:00",
                "window_end": "2026-10-02T00:00:00",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 422
        assert "explicit timezone offsets" in response.text
    finally:
        teardown_db()


def test_baseline_confidence_and_stability_require_complete_observation_coverage():
    setup_db()
    try:
        db = TestingSessionLocal()
        person = Person(id="person-covered-baseline", name="Covered baseline test")
        caregiver = Caregiver(
            id="caregiver-covered-baseline",
            name="Covered Baseline Caregiver",
            email="covered-baseline@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()
        now = datetime.now(timezone.utc)
        window_start = now - timedelta(days=320)
        window_end = now - timedelta(days=42)
        for i in range(5):
            db.add(CareEvent(
                person_id=person.id,
                event_type="fall",
                status="recorded",
                occurred_at=now - timedelta(days=200 + i * 30),
                title=f"Covered baseline fall {i + 1}",
                created_by_caregiver_id=caregiver.id,
            ))
        db.add(ObservationCoverage(
            id="complete-baseline-coverage",
            person_id=person.id,
            subject_type="fall",
            window_start=window_start,
            window_end=window_end,
            coverage_status="covered",
            source_evidence_ids=[],
            rationale="The domain was actively observed throughout the baseline interval.",
            recorded_by_caregiver_id=caregiver.id,
        ))
        db.commit()
        from app.services.baseline import build_baseline
        baseline = build_baseline(db, person.id, "fall", None, now)
        assert baseline is not None
        assert baseline.confidence.value == "high"
        assert baseline.is_stable is True
        assert baseline.observation_density == pytest.approx(1.0)
        assert baseline.documentation_bias_flag is False
        db.close()
    finally:
        teardown_db()


def test_baseline_is_revised_when_observation_coverage_becomes_known():
    setup_db()
    try:
        db = TestingSessionLocal()
        person = Person(id="person-baseline-revision", name="Baseline revision test")
        caregiver = Caregiver(
            id="caregiver-baseline-revision",
            name="Baseline Revision Caregiver",
            email="baseline-revision@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()
        now = datetime.now(timezone.utc)
        for i in range(5):
            db.add(CareEvent(
                id=f"baseline-revision-event-{i}",
                person_id=person.id,
                event_type="fall",
                status="recorded",
                occurred_at=now - timedelta(days=200 + i * 30),
                title=f"Baseline revision fall {i + 1}",
                created_by_caregiver_id=caregiver.id,
            ))
        db.commit()
        from app.services.baseline import build_baseline
        original = build_baseline(db, person.id, "fall", None, now)
        assert original is not None
        assert original.is_stable is False
        assert original.confidence.value == "low"

        db.add(ObservationCoverage(
            id="baseline-revision-coverage",
            person_id=person.id,
            subject_type="fall",
            window_start=original.window_start,
            window_end=original.window_end,
            coverage_status="covered",
            source_evidence_ids=[],
            rationale="The caregiver explicitly confirms observation throughout the historical interval.",
            recorded_by_caregiver_id=caregiver.id,
        ))
        db.commit()
        revised = build_baseline(db, person.id, "fall", None, now)
        assert revised is not None
        assert revised.id != original.id
        assert revised.is_stable is True
        assert revised.confidence.value == "high"
        assert original.is_active is False
        assert original.superseded_by_baseline_id == revised.id
        assert db.query(Baseline).filter(
            Baseline.person_id == person.id, Baseline.is_active.is_(True)
        ).count() == 1
        db.close()
    finally:
        teardown_db()


def test_zero_baseline_rate_is_explained_without_infinite_percentage():
    setup_db()
    try:
        db = TestingSessionLocal()
        person = Person(id="person-zero-baseline", name="Zero baseline test")
        caregiver = Caregiver(
            id="caregiver-zero-baseline",
            name="Zero Baseline Caregiver",
            email="zero-baseline@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()
        now = datetime.now(timezone.utc)
        for i, days_ago in enumerate((7, 14)):
            db.add(CareEvent(
                id=f"zero-baseline-event-{i}",
                person_id=person.id,
                event_type="fall",
                status="recorded",
                occurred_at=now - timedelta(days=days_ago),
                title=f"Recorded fall {i + 1}",
                created_by_caregiver_id=caregiver.id,
            ))
        db.add_all([
            ObservationCoverage(
                id="zero-baseline-covered",
                person_id=person.id,
                subject_type="fall",
                window_start=now - timedelta(days=180),
                window_end=now - timedelta(days=42),
                coverage_status="covered",
                source_evidence_ids=[],
                rationale="The domain was actively observed throughout the baseline interval.",
                recorded_by_caregiver_id=caregiver.id,
            ),
            ObservationCoverage(
                id="zero-current-covered",
                person_id=person.id,
                subject_type="fall",
                window_start=now - timedelta(days=42),
                window_end=now,
                coverage_status="covered",
                source_evidence_ids=[],
                rationale="The domain was actively observed throughout the current interval.",
                recorded_by_caregiver_id=caregiver.id,
            ),
        ])
        db.commit()
        from app.services.change_detection import detect_event_changes
        changes = detect_event_changes(db, person.id, "fall", None, now)
        emergence = next(item for item in changes if item.change_type.value == "emergence")
        assert "Relative change is undefined because the baseline rate was zero" in emergence.explanation
        assert "inf%" not in emergence.explanation.lower()
        db.close()
    finally:
        teardown_db()


def test_pattern_detection_finds_short_window_cluster_from_distinct_events():
    setup_db()
    try:
        db = TestingSessionLocal()
        person = Person(id="person-cluster", name="Cluster Test Person")
        caregiver = Caregiver(
            id="caregiver-cluster",
            name="Cluster Test Caregiver",
            email="cluster@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()
        now = datetime.now(timezone.utc)
        for index, days_ago in enumerate((50, 5, 4, 3), start=1):
            db.add(CareEvent(
                person_id=person.id,
                event_type="fall",
                status="recorded",
                occurred_at=now - timedelta(days=days_ago),
                title=f"Source fall event {index}",
                created_by_caregiver_id=caregiver.id,
                evidence_ids=[f"evidence-cluster-{index}"],
            ))
        db.commit()

        from app.services.pattern_detection import detect_patterns
        patterns = detect_patterns(db, person.id, "fall", None, now)
        clusters = [item for item in patterns if item.pattern_type.value == "clustering"]
        assert len(clusters) == 1
        assert len(clusters[0].event_ids) == 3
        assert clusters[0].confidence is None
        assert "does not establish a cause" in " ".join(clusters[0].alternative_interpretations or [])
    finally:
        db.close()
        teardown_db()


def test_pattern_detail_returns_only_same_person_source_events():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db
        db = TestingSessionLocal()
        person = Person(id="pattern-detail-person", name="Pattern detail person")
        caregiver = Caregiver(
            id="pattern-detail-caregiver", name="Pattern detail caregiver",
            email="pattern-detail@example.com", relationship="Family", person_id=person.id,
        )
        other = Person(id="pattern-detail-other", name="Other person")
        other_caregiver = Caregiver(
            id="pattern-detail-other-caregiver", name="Other caregiver",
            email="pattern-detail-other@example.com", relationship="Family", person_id=other.id,
        )
        db.add_all([person, caregiver, other, other_caregiver])
        db.flush()
        now = datetime.now(timezone.utc)
        own_event = CareEvent(
            id="pattern-detail-own-event", person_id=person.id, event_type="mobility",
            status="recorded", occurred_at=now - timedelta(days=10), title="Own mobility observation",
            created_by_caregiver_id=caregiver.id, evidence_ids=["own-evidence"],
        )
        foreign_event = CareEvent(
            id="pattern-detail-foreign-event", person_id=other.id, event_type="mobility",
            status="recorded", occurred_at=now - timedelta(days=9), title="Foreign mobility observation",
            created_by_caregiver_id=other_caregiver.id, evidence_ids=["foreign-evidence"],
        )
        db.add_all([own_event, foreign_event])
        from app.core.change_enums import PatternType, PatternStrength, ChangeDirection
        pattern = Pattern(
            id="pattern-detail-id", person_id=person.id, subject_type="mobility", subject_id="",
            pattern_type=PatternType.RECURRENCE, pattern_strength=PatternStrength.EMERGING,
            event_ids=[own_event.id, foreign_event.id], change_ids=[],
            temporal_start=now - timedelta(days=10), temporal_end=now - timedelta(days=9),
            direction=ChangeDirection.UNKNOWN, confidence=None, evidence_count=2,
            alternative_interpretations=["Only same-person source records should be returned."],
        )
        db.add(pattern)
        db.commit()
        token = issue_scoped_session(db, person, caregiver)
        person_id = person.id
        db.close()

        response = TestClient(app).get(
            f"/api/v1/change/patterns/{person_id}/pattern-detail-id",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["pattern"]["id"] == "pattern-detail-id"
        assert [event["id"] for event in payload["events"]] == ["pattern-detail-own-event"]
        assert all(event["id"] != "pattern-detail-foreign-event" for event in payload["events"])
    finally:
        teardown_db()


def test_observation_coverage_api_persists_and_enforces_person_scope():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db
        db = TestingSessionLocal()
        person = Person(id="coverage-api-person", name="Coverage API Person")
        caregiver = Caregiver(
            id="coverage-api-caregiver", name="Coverage API Caregiver",
            relationship="Family", person_id=person.id,
        )
        other = Person(id="coverage-api-other", name="Other Care Recipient")
        other_caregiver = Caregiver(
            id="coverage-api-other-caregiver", name="Other Caregiver",
            relationship="Family", person_id=other.id,
        )
        db.add_all([person, caregiver, other, other_caregiver])
        db.commit()
        token = issue_scoped_session(db, person, caregiver)
        other_token = issue_scoped_session(db, other, other_caregiver)
        db.close()

        client = TestClient(app)
        end = datetime.now(timezone.utc) - timedelta(hours=1)
        start = end - timedelta(days=7)
        payload = {
            "person_id": person.id,
            "subject_type": "mobility",
            "window_start": start.isoformat(),
            "window_end": end.isoformat(),
            "coverage_status": "covered",
            "source_evidence_ids": [],
            "rationale": "Caregiver explicitly observed mobility throughout this interval.",
        }
        created = client.post(
            "/api/v1/change/coverage",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )
        assert created.status_code == 201, created.text
        assert created.json()["coverage_status"] == "covered"
        assert created.json()["person_id"] == person.id
        assert created.json()["window_start"].endswith("+00:00") or created.json()["window_start"].endswith("Z")
        assert created.json()["window_end"].endswith("+00:00") or created.json()["window_end"].endswith("Z")

        assessed = client.get(
            f"/api/v1/change/coverage/{person.id}",
            params={
                "subject_type": "mobility",
                "window_start": start.isoformat(),
                "window_end": end.isoformat(),
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert assessed.status_code == 200, assessed.text
        assert assessed.json()["coverage_status"] == "covered"
        assert assessed.json()["coverage_fraction"] == 1.0

        forbidden = client.get(
            f"/api/v1/change/coverage/{person.id}",
            params={
                "subject_type": "mobility",
                "window_start": start.isoformat(),
                "window_end": end.isoformat(),
            },
            headers={"Authorization": f"Bearer {other_token}"},
        )
        assert forbidden.status_code == 403, forbidden.text

        unauthenticated = client.get(
            f"/api/v1/change/coverage/{person.id}",
            params={
                "subject_type": "mobility",
                "window_start": start.isoformat(),
                "window_end": end.isoformat(),
            },
        )
        assert unauthenticated.status_code == 401, unauthenticated.text
    finally:
        teardown_db()


def test_pattern_detection_does_not_mix_source_events_across_subjects():
    setup_db()
    try:
        db = TestingSessionLocal()
        person = Person(id="person-pattern-scope", name="Pattern scope test")
        caregiver = Caregiver(
            id="caregiver-pattern-scope",
            name="Pattern scope caregiver",
            email="pattern-scope@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()
        now = datetime.now(timezone.utc)
        subject_a_ids = {"scope-a-1", "scope-a-2"}
        subject_b_ids = {"scope-b-1", "scope-b-2"}
        from app.core.change_enums import ChangeType, ChangeDirection
        for event_id, subject_id, days_ago in (
            ("scope-a-1", "subject-a", 20),
            ("scope-a-2", "subject-a", 5),
            ("scope-b-1", "subject-b", 18),
            ("scope-b-2", "subject-b", 3),
        ):
            db.add(CareEvent(
                id=event_id,
                person_id=person.id,
                event_type="mobility_change",
                status="recorded",
                occurred_at=now - timedelta(days=days_ago),
                title=f"Recorded mobility event {event_id}",
                created_by_caregiver_id=caregiver.id,
                evidence_ids=[],
            ))
            db.add(Change(
                id=f"change-{event_id}",
                person_id=person.id,
                subject_type="mobility_change",
                subject_id=subject_id,
                change_type=ChangeType.INCREASE,
                change_direction=ChangeDirection.INCREASING,
                previous_state="Earlier recorded state",
                current_state="Later recorded state",
                evidence_ids=[event_id],
            ))
        db.commit()
        from app.services.pattern_detection import detect_patterns
        patterns = detect_patterns(db, person.id, "mobility_change", "subject-a", now)
        assert patterns, "Two linked source events should allow a scoped recurrence assessment"
        assert all(set(pattern.event_ids).issubset(subject_a_ids) for pattern in patterns)
        assert all(not set(pattern.event_ids).intersection(subject_b_ids) for pattern in patterns)
        db.close()
    finally:
        teardown_db()


def test_observation_coverage_api_persists_and_assesses_across_requests():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db
        db = TestingSessionLocal()
        person = Person(id="coverage-api-person", name="Coverage API person")
        caregiver = Caregiver(
            id="coverage-api-caregiver",
            name="Coverage API caregiver",
            email="coverage-api@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()
        token = issue_scoped_session(db, person, caregiver)
        db.close()

        now = datetime.now(timezone.utc)
        start = now - timedelta(days=14)
        end = now - timedelta(minutes=1)
        client = TestClient(app)
        created = client.post(
            "/api/v1/change/coverage",
            json={
                "person_id": person.id,
                "subject_type": "mobility",
                "window_start": start.isoformat(),
                "window_end": end.isoformat(),
                "coverage_status": "covered",
                "source_evidence_ids": [],
                "rationale": "Caregiver explicitly observed mobility throughout this interval.",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert created.status_code == 201, created.text
        saved = created.json()
        assert saved["person_id"] == person.id
        assert saved["recorded_by_caregiver_id"] == caregiver.id

        assessed = client.get(
            f"/api/v1/change/coverage/{person.id}",
            params={
                "subject_type": "mobility",
                "window_start": start.isoformat(),
                "window_end": end.isoformat(),
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert assessed.status_code == 200, assessed.text
        body = assessed.json()
        assert body["coverage_status"] == "covered"
        assert body["coverage_fraction"] == 1.0
        assert any(record["id"] == saved["id"] for record in body["records"])
    finally:
        teardown_db()


def test_observation_coverage_api_enforces_bearer_person_scope():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db
        db = TestingSessionLocal()
        person_a = Person(id="coverage-scope-person-a", name="Person A")
        caregiver_a = Caregiver(
            id="coverage-scope-caregiver-a",
            name="Caregiver A",
            email="coverage-scope-a@example.com",
            relationship="Family",
            person_id=person_a.id,
        )
        person_b = Person(id="coverage-scope-person-b", name="Person B")
        caregiver_b = Caregiver(
            id="coverage-scope-caregiver-b",
            name="Caregiver B",
            email="coverage-scope-b@example.com",
            relationship="Family",
            person_id=person_b.id,
        )
        db.add_all([person_a, caregiver_a, person_b, caregiver_b])
        db.commit()
        token_a = issue_scoped_session(db, person_a, caregiver_a)
        db.close()

        now = datetime.now(timezone.utc)
        client = TestClient(app)
        unauthorized = client.get(
            f"/api/v1/change/coverage/{person_a.id}",
            params={
                "subject_type": "mobility",
                "window_start": (now - timedelta(days=2)).isoformat(),
                "window_end": (now - timedelta(days=1)).isoformat(),
            },
        )
        assert unauthorized.status_code == 401

        cross_person_read = client.get(
            f"/api/v1/change/coverage/{person_b.id}",
            params={
                "subject_type": "mobility",
                "window_start": (now - timedelta(days=2)).isoformat(),
                "window_end": (now - timedelta(days=1)).isoformat(),
            },
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert cross_person_read.status_code == 403

        cross_person_write = client.post(
            "/api/v1/change/coverage",
            json={
                "person_id": person_b.id,
                "subject_type": "mobility",
                "window_start": (now - timedelta(days=2)).isoformat(),
                "window_end": (now - timedelta(days=1)).isoformat(),
                "coverage_status": "covered",
                "source_evidence_ids": [],
                "rationale": "This write must be rejected for a different care record.",
            },
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert cross_person_write.status_code == 403
    finally:
        teardown_db()


def test_future_dated_events_are_not_counted_as_occurrences_yet():
    setup_db()
    try:
        from app.services.baseline import get_longitudinal_event_counts

        db = TestingSessionLocal()
        person = Person(id="future-event-person", name="Future event test")
        caregiver = Caregiver(
            id="future-event-caregiver", name="Future event caregiver",
            email="future-event@example.com", relationship="Family", person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()
        now = datetime.now(timezone.utc)
        db.add_all([
            CareEvent(
                id="past-event", person_id=person.id, event_type="mobility",
                status="recorded", occurred_at=now - timedelta(days=100),
                title="Past mobility event", created_by_caregiver_id=caregiver.id,
            ),
            CareEvent(
                id="future-event", person_id=person.id, event_type="mobility",
                status="recorded", occurred_at=now + timedelta(days=2),
                title="Future scheduled item", created_by_caregiver_id=caregiver.id,
            ),
        ])
        db.commit()
        counts = get_longitudinal_event_counts(db, person.id, "mobility", None, now)
        assert [event.id for event in counts["all_events"]] == ["past-event"]
        assert counts["current_window_count"] == 0
        assert counts["historical_count"] == 1
        db.close()
    finally:
        teardown_db()


def test_truncated_longitudinal_history_never_claims_a_stable_baseline(monkeypatch):
    setup_db()
    try:
        import app.services.baseline as baseline_service

        monkeypatch.setattr(baseline_service, "MAX_LONGITUDINAL_EVENTS", 2)
        db = TestingSessionLocal()
        person = Person(id="bounded-history-person", name="Bounded history test")
        caregiver = Caregiver(
            id="bounded-history-caregiver", name="Bounded history caregiver",
            email="bounded-history@example.com", relationship="Family", person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()
        now = datetime.now(timezone.utc)
        for event_id, days_ago in (
            ("bounded-old-event", 150),
            ("bounded-baseline-event", 100),
            ("bounded-current-event", 5),
        ):
            db.add(CareEvent(
                id=event_id, person_id=person.id, event_type="mobility",
                status="recorded", occurred_at=now - timedelta(days=days_ago),
                title=event_id, created_by_caregiver_id=caregiver.id,
            ))
        db.add(ObservationCoverage(
            id="bounded-history-coverage", person_id=person.id, subject_type="mobility",
            window_start=now - timedelta(days=100), window_end=now - timedelta(days=42),
            coverage_status="covered", source_evidence_ids=[],
            rationale="The included baseline interval was explicitly observed.",
            recorded_by_caregiver_id=caregiver.id,
        ))
        db.commit()
        counts = baseline_service.get_longitudinal_event_counts(db, person.id, "mobility", None, now)
        assert counts["history_truncated"] is True
        assert {event.id for event in counts["all_events"]} == {
            "bounded-baseline-event", "bounded-current-event"
        }
        baseline = baseline_service.build_baseline(db, person.id, "mobility", None, now)
        assert baseline is not None
        assert baseline.confidence.value == "low"
        assert baseline.is_stable is False
        assert baseline.frequency_per_period is None
        assert baseline.documentation_bias_flag is True
        assert "bounded to the most recent 2" in baseline.state_value
        assert baseline.history_completeness == "truncated"
        db.close()
    finally:
        teardown_db()


def test_truncated_source_history_withholds_rate_and_frequency_patterns(monkeypatch):
    setup_db()
    try:
        import app.services.pattern_detection as pattern_service

        monkeypatch.setattr(pattern_service, "MAX_PATTERN_SOURCE_EVENTS", 2)
        db = TestingSessionLocal()
        person = Person(id="bounded-pattern-person", name="Bounded pattern test")
        caregiver = Caregiver(
            id="bounded-pattern-caregiver", name="Bounded pattern caregiver",
            email="bounded-pattern@example.com", relationship="Family", person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()
        now = datetime.now(timezone.utc)
        for event_id, days_ago in (
            ("bounded-pattern-event-1", 20),
            ("bounded-pattern-event-2", 10),
            ("bounded-pattern-event-3", 1),
        ):
            db.add(CareEvent(
                id=event_id, person_id=person.id, event_type="fall",
                status="recorded", occurred_at=now - timedelta(days=days_ago),
                title=event_id, created_by_caregiver_id=caregiver.id,
            ))
        db.add(ObservationCoverage(
            id="bounded-pattern-coverage", person_id=person.id, subject_type="fall",
            window_start=now - timedelta(days=90), window_end=now,
            coverage_status="covered", source_evidence_ids=[],
            rationale="The benchmark interval was explicitly observed.",
            recorded_by_caregiver_id=caregiver.id,
        ))
        db.commit()
        patterns = pattern_service.detect_patterns(db, person.id, "fall", None, now)
        recurrence = next(item for item in patterns if item.pattern_type.value == "recurrence")
        assert recurrence.rate_per_period is None
        assert recurrence.pattern_metrics["normalized_rate_available"] is False
        assert recurrence.pattern_metrics["source_history_truncated"] is True
        assert any("denominator may be incomplete" in note for note in recurrence.alternative_interpretations or [])
        assert all(item.pattern_type.value not in {"frequency_increase", "frequency_decrease"} for item in patterns)
        db.close()
    finally:
        teardown_db()


def test_truncated_baseline_history_does_not_claim_emergence(monkeypatch):
    setup_db()
    try:
        from app.services.change_detection import detect_event_changes
        import app.services.baseline as baseline_service

        monkeypatch.setattr(baseline_service, "MAX_LONGITUDINAL_EVENTS", 2)
        db = TestingSessionLocal()
        person = Person(id="bounded-emergence-person", name="Bounded emergence test")
        caregiver = Caregiver(
            id="bounded-emergence-caregiver", name="Bounded emergence caregiver",
            email="bounded-emergence@example.com", relationship="Family", person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()
        now = datetime.now(timezone.utc)
        for event_id, days_ago in (
            ("bounded-emergence-event-1", 20),
            ("bounded-emergence-event-2", 10),
            ("bounded-emergence-event-3", 1),
        ):
            db.add(CareEvent(
                id=event_id, person_id=person.id, event_type="mobility",
                status="recorded", occurred_at=now - timedelta(days=days_ago),
                title=event_id, created_by_caregiver_id=caregiver.id,
            ))
        db.add(ObservationCoverage(
            id="bounded-emergence-coverage", person_id=person.id, subject_type="mobility",
            window_start=now - timedelta(days=180), window_end=now - timedelta(days=42),
            coverage_status="covered", source_evidence_ids=[],
            rationale="The stated historical interval was explicitly observed.",
            recorded_by_caregiver_id=caregiver.id,
        ))
        db.commit()
        changes = detect_event_changes(db, person.id, "mobility", None, now)
        assert all(change.change_type.value != "emergence" for change in changes)
        recurrence_changes = [change for change in changes if change.change_type.value == "recurrence"]
        assert recurrence_changes
        assert recurrence_changes[0].previous_count is None
        assert "unavailable because eligible source history was truncated" in recurrence_changes[0].previous_state
        db.close()
    finally:
        teardown_db()


def test_observation_coverage_api_persists_and_assesses_scoped_intervals():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db
        db = TestingSessionLocal()
        person = Person(id="coverage-api-person", name="Coverage API Person")
        caregiver = Caregiver(
            id="coverage-api-caregiver", name="Coverage API Caregiver",
            email="coverage-api@example.com", relationship="Family", person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()
        token = issue_scoped_session(db, person, caregiver)
        db.close()

        client = TestClient(app)
        end = datetime.now(timezone.utc) - timedelta(hours=1)
        start = end - timedelta(days=7)
        payload = {
            "person_id": person.id,
            "subject_type": "mobility",
            "window_start": start.isoformat(),
            "window_end": end.isoformat(),
            "coverage_status": "covered",
            "source_evidence_ids": [],
            "rationale": "Caregiver explicitly observed this domain during the interval.",
        }
        saved = client.post(
            "/api/v1/change/coverage",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )
        assert saved.status_code == 201, saved.text
        assert saved.json()["coverage_status"] == "covered"
        assert saved.json()["recorded_by_caregiver_id"] == caregiver.id

        assessed = client.get(
            f"/api/v1/change/coverage/{person.id}",
            params={
                "subject_type": "mobility",
                "window_start": start.isoformat(),
                "window_end": end.isoformat(),
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert assessed.status_code == 200, assessed.text
        assert assessed.json()["coverage_status"] == "covered"
        assert assessed.json()["coverage_fraction"] == 1.0
        assert len(assessed.json()["records"]) == 1

        forbidden = client.get(
            "/api/v1/change/coverage/another-person",
            params={
                "subject_type": "mobility",
                "window_start": start.isoformat(),
                "window_end": end.isoformat(),
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert forbidden.status_code == 403
    finally:
        teardown_db()


def test_observation_coverage_api_rejects_future_or_invalid_windows():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db
        db = TestingSessionLocal()
        person = Person(id="coverage-window-person", name="Coverage Window Person")
        caregiver = Caregiver(
            id="coverage-window-caregiver", name="Coverage Window Caregiver",
            email="coverage-window@example.com", relationship="Family", person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()
        token = issue_scoped_session(db, person, caregiver)
        db.close()

        client = TestClient(app)
        now = datetime.now(timezone.utc)
        future_payload = {
            "person_id": person.id,
            "subject_type": "mobility",
            "window_start": now.isoformat(),
            "window_end": (now + timedelta(hours=1)).isoformat(),
            "coverage_status": "covered",
            "source_evidence_ids": [],
            "rationale": "Attempted future coverage.",
        }
        future = client.post(
            "/api/v1/change/coverage",
            json=future_payload,
            headers={"Authorization": f"Bearer {token}"},
        )
        assert future.status_code == 422

        invalid = client.get(
            f"/api/v1/change/coverage/{person.id}",
            params={
                "subject_type": "mobility",
                "window_start": now.isoformat(),
                "window_end": (now - timedelta(hours=1)).isoformat(),
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert invalid.status_code == 422
    finally:
        teardown_db()


def test_cross_domain_cooccurrence_uses_real_events_and_makes_no_causal_claim():
    setup_db()
    try:
        db = TestingSessionLocal()
        person = Person(id="cross-domain-person", name="Cross-domain test person")
        caregiver = Caregiver(
            id="cross-domain-caregiver", name="Cross-domain test caregiver",
            email="cross-domain@example.com", relationship="Family", person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()
        now = datetime.now(timezone.utc)
        events = [
            CareEvent(
                person_id=person.id, event_type="mobility_observation", status="recorded",
                occurred_at=now - timedelta(days=2), title="Recorded mobility observation",
                created_by_caregiver_id=caregiver.id,
            ),
            CareEvent(
                person_id=person.id, event_type="sleep_disruption", status="recorded",
                occurred_at=now - timedelta(days=1), title="Recorded sleep observation",
                created_by_caregiver_id=caregiver.id,
            ),
            CareEvent(
                person_id=person.id, event_type="routine_disruption", status="recorded",
                occurred_at=now - timedelta(hours=2), title="Recorded routine observation",
                created_by_caregiver_id=caregiver.id,
            ),
        ]
        db.add_all(events)
        db.commit()

        from app.services.pattern_detection import detect_cross_domain_patterns
        patterns = detect_cross_domain_patterns(db, person.id, now)
        db.commit()
        assert len(patterns) == 1
        pattern = patterns[0]
        assert pattern.subject_type == "cross_domain"
        assert pattern.pattern_type.value == "convergence"
        assert set(pattern.event_ids) == {event.id for event in events}
        assert pattern.pattern_metrics["distinct_domain_count"] == 3
        assert pattern.pattern_metrics["normalized_rate_available"] is False
        explanation = " ".join(pattern.alternative_interpretations).lower()
        assert "does not establish a shared cause" in explanation
        assert pattern.confidence is None
        db.close()
    finally:
        teardown_db()


def test_cross_domain_pattern_retires_only_after_complete_scan_no_longer_supports_it():
    setup_db()
    try:
        db = TestingSessionLocal()
        person = Person(id="cross-domain-retire-person", name="Cross-domain lifecycle person")
        caregiver = Caregiver(
            id="cross-domain-retire-caregiver", name="Cross-domain lifecycle caregiver",
            email="cross-domain-retire@example.com", relationship="Family", person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()
        now = datetime.now(timezone.utc)
        events = [
            CareEvent(
                person_id=person.id, event_type=kind, status="recorded",
                occurred_at=now - timedelta(hours=offset), title=f"Recorded {kind}",
                created_by_caregiver_id=caregiver.id,
            )
            for kind, offset in (
                ("mobility_observation", 48),
                ("sleep_disruption", 24),
                ("routine_disruption", 2),
            )
        ]
        db.add_all(events)
        db.commit()

        from app.services.pattern_detection import detect_cross_domain_patterns
        first = detect_cross_domain_patterns(db, person.id, now)
        db.commit()
        assert len(first) == 1
        pattern_id = first[0].id

        for event in events:
            event.status = "invalidated"
        db.commit()
        second = detect_cross_domain_patterns(db, person.id, now)
        db.commit()
        assert second == []
        retired = db.query(Pattern).filter(Pattern.id == pattern_id).one()
        assert retired.is_active is False
        assert any("does not prove" in note for note in retired.alternative_interpretations)
        db.close()
    finally:
        teardown_db()

def test_authenticated_reassessment_persists_cross_domain_convergence():
    setup_db()
    try:
        from app.core.database import get_db
        from app.core.change_enums import PatternType
        app.dependency_overrides[get_db] = override_get_db

        db = TestingSessionLocal()
        person = Person(id="cross-domain-api-person", name="Cross-domain API person")
        caregiver = Caregiver(
            id="cross-domain-api-caregiver", name="Cross-domain API caregiver",
            email="cross-domain-api@example.com", relationship="Family", person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()
        token = issue_scoped_session(db, person, caregiver)
        now = datetime.now(timezone.utc)
        for event_id, event_type, days_ago in (
            ("cross-domain-api-fall", "fall", 5),
            ("cross-domain-api-sleep", "sleep_disruption", 4),
            ("cross-domain-api-mobility", "mobility", 2),
        ):
            db.add(CareEvent(
                id=event_id, person_id=person.id, event_type=event_type,
                status="recorded", occurred_at=now - timedelta(days=days_ago),
                title=f"Recorded {event_type} event", created_by_caregiver_id=caregiver.id,
                evidence_ids=[],
            ))
        db.commit()
        db.close()

        response = TestClient(app).post(
            f"/api/v1/change/changes/reassess?person_id={person.id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200, response.text

        db = TestingSessionLocal()
        convergence = (
            db.query(Pattern)
            .filter(
                Pattern.person_id == person.id,
                Pattern.subject_type == "cross_domain",
                Pattern.pattern_type == PatternType.CONVERGENCE,
                Pattern.is_active.is_(True),
            )
            .one()
        )
        assert set(convergence.event_ids) == {
            "cross-domain-api-fall", "cross-domain-api-sleep", "cross-domain-api-mobility"
        }
        assert convergence.confidence is None
        assert any(
            "does not establish" in note.lower()
            for note in (convergence.alternative_interpretations or [])
        )
        db.close()
    finally:
        teardown_db()

def test_invalidating_last_source_event_retires_active_emergence_assessment():
    setup_db()
    try:
        db = TestingSessionLocal()
        person = Person(id="invalidated-source-person", name="Invalidated source test")
        caregiver = Caregiver(
            id="invalidated-source-caregiver", name="Invalidated source caregiver",
            email="invalidated-source@example.com", relationship="Family", person_id=person.id,
        )
        event = CareEvent(
            id="invalidated-source-event", person_id=person.id, event_type="fall",
            status="recorded", occurred_at=datetime.now(timezone.utc) - timedelta(days=2),
            title="Recorded fall", created_by_caregiver_id=caregiver.id, evidence_ids=["source-evidence"],
        )
        db.add_all([person, caregiver, event])
        db.commit()

        from app.services.change_detection import detect_event_changes
        changes = detect_event_changes(db, person.id, "fall", None)
        emergence = next(change for change in changes if change.change_type.value == "emergence")
        db.commit()
        assert emergence.is_active is True

        event.status = "invalidated"
        db.commit()
        assert detect_event_changes(db, person.id, "fall", None) == []
        db.commit()

        historical = db.query(Change).filter_by(id=emergence.id).one()
        assert historical.is_active is False
        assert "no eligible source events remain" in historical.explanation.lower()
        db.close()
    finally:
        teardown_db()



def test_numeric_observation_change_requires_comparable_source_bound_measurements():
    setup_db()
    try:
        db = TestingSessionLocal()
        person = Person(id="numeric-change-person", name="Numeric change person")
        caregiver = Caregiver(
            id="numeric-change-caregiver", name="Numeric change caregiver",
            email="numeric-change@example.com", relationship="Family", person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()
        now = datetime.now(timezone.utc)
        source_rows = [
            Evidence(
                id="numeric-evidence-prior", person_id=person.id, type="measurement",
                source_text="Instrument recorded 10 kg.", uploaded_by_caregiver_id=caregiver.id,
            ),
            Evidence(
                id="numeric-evidence-current", person_id=person.id, type="measurement",
                source_text="Instrument recorded 12 kg.", uploaded_by_caregiver_id=caregiver.id,
            ),
        ]
        db.add_all(source_rows)
        db.flush()
        prior = EvidenceObservation(
            id="numeric-observation-prior", person_id=person.id, evidence_id=source_rows[0].id,
            observation_kind="instrument_measurement", epistemic_status="observed",
            subject_label="Person", attribute="weight", value_text="10 kg",
            normalized_value="10", units="kg", observed_at=now - timedelta(days=7),
            source_passage="10 kg", source_span_start=20, source_span_end=25,
            extraction_method="manual", extraction_version="test-v1",
            created_by_caregiver_id=caregiver.id,
        )
        current = EvidenceObservation(
            id="numeric-observation-current", person_id=person.id, evidence_id=source_rows[1].id,
            observation_kind="instrument_measurement", epistemic_status="observed",
            subject_label="Person", attribute="weight", value_text="12 kg",
            normalized_value="12", units="kg", observed_at=now,
            source_passage="12 kg", source_span_start=20, source_span_end=25,
            extraction_method="manual", extraction_version="test-v1",
            created_by_caregiver_id=caregiver.id,
        )
        db.add_all([prior, current])
        db.commit()

        from app.services.change_detection import detect_numeric_observation_changes
        changes = detect_numeric_observation_changes(db, person.id, now)
        db.commit()
        assert len(changes) == 1
        change = changes[0]
        assert change.change_type.value == "transition"
        assert change.change_direction.value == "unknown"
        assert change.previous_state == "10 kg"
        assert change.current_state == "12 kg"
        assert change.magnitude == pytest.approx(20.0)
        assert change.confidence is None
        assert set(change.evidence_ids) == {prior.id, current.id}
        assert "does not establish" in change.explanation

        repeated = detect_numeric_observation_changes(db, person.id, now)
        db.commit()
        assert [row.id for row in repeated] == [change.id]
        db.close()
    finally:
        teardown_db()


def test_numeric_observation_change_with_zero_prior_value_has_no_relative_ratio():
    setup_db()
    try:
        db = TestingSessionLocal()
        person = Person(id="numeric-zero-person", name="Numeric zero person")
        caregiver = Caregiver(
            id="numeric-zero-caregiver", name="Numeric zero caregiver",
            email="numeric-zero@example.com", relationship="Family", person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()
        now = datetime.now(timezone.utc)
        rows = []
        for idx, (value, days_ago) in enumerate((("0", 2), ("5", 0))):
            evidence = Evidence(
                id=f"numeric-zero-evidence-{idx}", person_id=person.id, type="measurement",
                source_text=f"Instrument recorded {value} units.", uploaded_by_caregiver_id=caregiver.id,
            )
            db.add(evidence)
            db.flush()
            observation = EvidenceObservation(
                id=f"numeric-zero-observation-{idx}", person_id=person.id, evidence_id=evidence.id,
                observation_kind="instrument_measurement", epistemic_status="observed",
                subject_label="Person", attribute="score", value_text=f"{value} units",
                normalized_value=value, units="units", observed_at=now - timedelta(days=days_ago),
                source_passage=f"{value} units", source_span_start=20, source_span_end=30,
                extraction_method="manual", extraction_version="test-v1",
                created_by_caregiver_id=caregiver.id,
            )
            db.add(observation)
            rows.append(observation)
        db.commit()

        from app.services.change_detection import detect_numeric_observation_changes
        changes = detect_numeric_observation_changes(db, person.id, now)
        db.commit()
        assert len(changes) == 1
        assert changes[0].magnitude is None
        assert "Relative change is undefined" in changes[0].explanation
        assert set(changes[0].evidence_ids) == {row.id for row in rows}
        db.close()
    finally:
        teardown_db()
