from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.main as main
from app.core.database import Base
from app.models.care import Person
from app.models.memory import ReasoningMemory
from app.services.learning import LearningEngine


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
