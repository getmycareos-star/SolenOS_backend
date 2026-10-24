from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.main as main
from app.core.database import Base
from app.models.care import Person
from app.models.memory import ReasoningMemory
from app.services.learning import LearningEngine
from app.services.time_intelligence import parse_natural_language_time
from datetime import datetime, timezone


def test_feedback_memory_does_not_claim_perfect_confidence():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    try:
        person = Person(id="confidence-unknown-person", name="Test Person")
        db.add(person)
        db.commit()

        LearningEngine(db).record_feedback(
            person_id=person.id,
            category="care_preference",
            detail="Caregiver-provided feedback for confidence regression test.",
            caregiver_id="test-caregiver",
        )
        memory = db.query(ReasoningMemory).filter_by(person_id=person.id).one()
        assert memory.confidence is None
        summary = LearningEngine(db).get_reasoning_summary(person.id)
        assert summary["preferences"] == []
        assert summary["confirmed_facts"] == []
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_temporal_parser_returns_unknown_confidence_not_an_uncalibrated_score():
    result = parse_natural_language_time(
        "today",
        datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc),
    )
    assert result["parsed_datetime"] is not None
    assert result["confidence"] is None
    assert result["provenance"] == "ai_estimated"
