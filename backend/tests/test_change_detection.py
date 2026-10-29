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
from app.models.care import Person, Caregiver, CareEvent
from app.models.anonymous_session import AnonymousSession
from app.models.care_continuity import CareAssertion
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
        assert changes[0].previous_count == 0
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
            window_start=earlier,
            window_end=later,
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
        assert recurrence.rate_per_period == pytest.approx(2.0)
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
            f"/change/coverage/{person_id}",
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
