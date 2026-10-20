from datetime import datetime, timezone, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.core.database import Base
from app.models.identity import Person
from app.models.caregiver import Caregiver
from app.models.care_event import CareEvent as CareEventModel
from app.services.baseline import build_baseline, compare_to_baseline, get_longitudinal_event_counts
from app.models.change import Baseline
from app.core.change_enums import BaselineConfidence

# Setup in-memory database
engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base.metadata.create_all(bind=engine)

def test_build_baseline():
    db = TestingSessionLocal()
    try:
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
        # Add some events
        for i in range(5):
            event = CareEventModel(
                person_id=person.id,
                event_type="fall",
                status="recorded",
                occurred_at=now - timedelta(days=200 + i * 30),
                title=f"Fall {i+1}",
                created_by_caregiver_id=caregiver.id,
            )
            db.add(event)
        db.commit()
        # Call build_baseline with the new signature: (db, person_id, event_type, subject_id, now)
        baseline = build_baseline(db, person.id, "fall", None, now)
        assert baseline is not None
        assert baseline.event_count == 5
        # Check confidence: 5 events -> MODERATE
        assert baseline.confidence == BaselineConfidence.MODERATE
        print("Baseline test passed: event_count=5, confidence=MODERATE")
    finally:
        db.close()

def test_compare_to_baseline():
    # We'll create a baseline object manually
    baseline = Baseline(
        person_id="person-1",
        subject_type="fall",
        subject_id="person-1",
        baseline_type=BaselineType.PERSONAL_HISTORICAL,
        window_start=datetime.now(timezone.utc) - timedelta(days=365),
        window_end=datetime.now(timezone.utc),
        event_count=10,
        frequency_per_period=10.0,
        state_value=None,
        confidence=BaselineConfidence.HIGH,
        is_stable=True,
        observation_density=10.0/365.0,
        documentation_bias_flag=False,
        evidence_ids=[],
    )
    # Compare: baseline has 10 events, current_count is 15 in a 30-day window
    result = compare_to_baseline(baseline, current_count=15, window_days=30)
    assert result["baseline_event_count"] == 10
    assert result["relative_change_percent"] == 50.0  # (15-10)/10*100
    assert result["baseline_confidence"] == "high"
    print("Compare to baseline test passed: relative_change=50%")

def test_get_longitudinal_event_counts():
    db = TestingSessionLocal()
    try:
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
        # Add events: 2 in the last 30 days, 3 more in the older part of the 365-day window (but not in the last 30)
        for i in range(5):
            if i < 2:
                # recent
                occurred_at = now - timedelta(days=10 + i * 5)
            else:
                # older but within 365 days
                occurred_at = now - timedelta(days=100 + i * 30)
            event = CareEventModel(
                person_id=person.id,
                event_type="fall",
                status="recorded",
                occurred_at=occurred_at,
                title=f"Fall {i+1}",
                created_by_caregiver_id=caregiver.id,
            )
            db.add(event)
        db.commit()
        counts = get_longitudinal_event_counts(db, person.id, "fall", None, now)
        # current_window_count: events in last 30 days -> 2
        assert counts["current_window_count"] == 2
        # historical_count: events in baseline window (365 days) but not in current window -> 3
        assert counts["historical_count"] == 3
        # recent_events should be 2 events
        assert len(counts["recent_events"]) == 2
        print("Longitudinal counts test passed: current=2, historical=3")
    finally:
        db.close()

if __name__ == "__main__":
    test_build_baseline()
    test_compare_to_baseline()
    test_get_longitudinal_event_counts()
    print("All baseline tests passed!")
