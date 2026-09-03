from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.main import app
from app.models.care import Person, Caregiver, CareEvent
from app.models.change import Change

SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


def setup_db():
    Base.metadata.create_all(bind=engine)


def teardown_db():
    Base.metadata.drop_all(bind=engine)
    app.dependency_overrides.clear()


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
        assert baseline.confidence.value == "high"
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
        assert changes[0].change_type.value == "onset"
        assert changes[0].previous_count == 0
        assert changes[0].current_count == 3
        db.close()
    finally:
        teardown_db()


def test_pattern_detection_recurrence():
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
        from app.core.change_enums import ChangeType, ChangeDirection
        from app.services.pattern_detection import detect_patterns
        for i in range(2):
            change = Change(
                person_id=person.id,
                subject_type="fall",
                subject_id="",
                change_type=ChangeType.INCREASE,
                change_direction=ChangeDirection.INCREASING,
                previous_state="low",
                current_state="high",
                previous_count=1,
                current_count=2 + i,
                confidence=0.7,
                evidence_ids=[],
                created_at=now - timedelta(days=30 + i * 30),
            )
            db.add(change)
        db.commit()
        patterns = detect_patterns(db, person.id, "fall", None, now)
        assert len(patterns) >= 1
        pattern_types = [p.pattern_type.value for p in patterns]
        assert "frequency_increase" in pattern_types or "recurrence" in pattern_types
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
        db.close()

        client = TestClient(app)
        response = client.get(
            "/api/v1/change/changes/detect",
            params={"person_id": person_id, "event_type": "fall"},
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 1
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
        db.close()

        client = TestClient(app)
        response = client.get(
            "/api/v1/change/situations/detect",
            params={"person_id": person_id},
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 1
    finally:
        teardown_db()
