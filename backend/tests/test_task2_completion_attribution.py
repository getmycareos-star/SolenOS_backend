from datetime import datetime, timezone
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models.action import Action as ActionModel
from app.models.care import Person, Caregiver, Evidence
from app.schemas.action import ActionCompletionEvidence
from app.services.action_lifecycle import ActionLifecycleService


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    person = Person(id="person-a", name="Person A")
    caregiver = Caregiver(
        id="caregiver-a", name="Caregiver A", email="caregiver-a@example.test",
        relationship="family", person_id="person-a",
    )
    evidence = Evidence(
        id="evidence-a", person_id="person-a", type="caregiver_report",
        source_text="Care task was completed", uploaded_by_caregiver_id="caregiver-a",
    )
    action = ActionModel(
        id="action-a", person_id="person-a", source_evidence_id="evidence-a",
        source_passage="Follow up with clinic", original_text="Follow up with clinic",
        normalized_action="Follow up with clinic", created_by_caregiver_id="caregiver-a",
        status="active",
    )
    session.add_all([person, caregiver, evidence, action])
    session.commit()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_completion_persists_timestamp_caregiver_and_notes(db):
    result = ActionLifecycleService().record_completion(
        db.query(ActionModel).filter_by(id="action-a").one(),
        ActionCompletionEvidence(
            caregiver_id="caregiver-a", evidence_ids=["evidence-a"],
            completion_confidence=0.8, notes="Confirmed by phone.",
        ),
        db,
    )
    assert result.status == "completed"
    assert result.completed_at is not None
    assert result.completed_by_caregiver_id == "caregiver-a"
    assert result.completion_notes == "Confirmed by phone."
    assert result.completion_evidence_ids == ["evidence-a"]


def test_completion_rejects_caregiver_from_another_person(db):
    other_person = Person(id="person-b", name="Person B")
    other_caregiver = Caregiver(
        id="caregiver-b", name="Caregiver B", email="caregiver-b@example.test",
        relationship="family", person_id="person-b",
    )
    db.add_all([other_person, other_caregiver])
    db.commit()
    with pytest.raises(ValueError, match="not linked"):
        ActionLifecycleService().record_completion(
            db.query(ActionModel).filter_by(id="action-a").one(),
            ActionCompletionEvidence(caregiver_id="caregiver-b", evidence_ids=[], completion_confidence=0.7),
            db,
        )


def test_completion_rejects_cross_person_evidence(db):
    other_person = Person(id="person-b", name="Person B")
    other_caregiver = Caregiver(
        id="caregiver-b", name="Caregiver B", email="caregiver-b@example.test",
        relationship="family", person_id="person-b",
    )
    other_evidence = Evidence(
        id="evidence-b", person_id="person-b", type="caregiver_report",
        source_text="Other person's evidence", uploaded_by_caregiver_id="caregiver-b",
    )
    db.add_all([other_person, other_caregiver, other_evidence])
    db.commit()
    with pytest.raises(ValueError, match="same care recipient"):
        ActionLifecycleService().record_completion(
            db.query(ActionModel).filter_by(id="action-a").one(),
            ActionCompletionEvidence(caregiver_id="caregiver-a", evidence_ids=["evidence-b"], completion_confidence=0.7),
            db,
        )


def test_completion_cannot_be_overwritten(db):
    service = ActionLifecycleService()
    action = db.query(ActionModel).filter_by(id="action-a").one()
    service.record_completion(
        action,
        ActionCompletionEvidence(caregiver_id="caregiver-a", evidence_ids=[], completion_confidence=0.8),
        db,
    )
    with pytest.raises(ValueError, match="already completed"):
        service.record_completion(
            action,
            ActionCompletionEvidence(caregiver_id="caregiver-a", evidence_ids=[], completion_confidence=0.9),
            db,
        )
