from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session as OrmSession, sessionmaker
from sqlalchemy.pool import StaticPool
from pydantic import ValidationError

from app.core.database import Base
from app.models.care import Person, Caregiver, CareEvent, Evidence
from app.models.care_continuity import CareAssertion, CareStateRevision, CareActionOutcome
from app.models.uncertainty import Contradiction, OpenQuestion
from app.core.uncertainty_enums import Priority
from app.schemas.care_continuity import CareAssertionCreate, CareActionOutcomeCreate
from app.services.care_continuity import (
    create_assertion, create_action_outcome, get_freshness, next_best_questions,
)


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()
    person = Person(id="person-1", name="Care recipient")
    caregiver = Caregiver(
        id="caregiver-1", name="Caregiver", relationship="family",
        person_id="person-1", timezone="UTC",
    )
    session.add_all([person, caregiver])
    session.commit()
    session.add_all([
        Evidence(id="ev-1", person_id="person-1", type="note", source_text="First source", uploaded_by_caregiver_id="caregiver-1"),
        Evidence(id="ev-2", person_id="person-1", type="note", source_text="Second source", uploaded_by_caregiver_id="caregiver-1"),
    ])
    session.commit()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_care_state_changes_write_immutable_before_after_revisions(db: OrmSession):
    event = CareEvent(
        id="event-1", person_id="person-1", event_type="observation",
        occurred_at=datetime(2026, 10, 20, tzinfo=timezone.utc),
        title="Ate half of lunch", description="Reported by caregiver",
        evidence_ids=["evidence-1"], created_by_caregiver_id="caregiver-1",
    )
    db.add(event)
    db.commit()
    event.title = "Ate one quarter of lunch"
    db.commit()

    revisions = db.query(CareStateRevision).filter(
        CareStateRevision.target_type == "care_events",
        CareStateRevision.target_id == "event-1",
    ).order_by(CareStateRevision.revision_number.asc()).all()
    assert [item.revision_number for item in revisions] == [1, 2]
    assert revisions[0].change_kind == "created"
    assert revisions[0].before_state is None
    assert '"Ate half of lunch"' in revisions[0].after_state
    assert revisions[1].change_kind == "updated"
    assert '"Ate half of lunch"' in revisions[1].before_state
    assert '"Ate one quarter of lunch"' in revisions[1].after_state
    assert revisions[1].evidence_ids == ["evidence-1"]


def _assertion(person_id: str, value: str, evidence_id: str):
    return CareAssertionCreate(
        person_id=person_id, subject_type="medication", subject_id="med-1",
        field="dose_reported", asserted_value=value, source_label="caregiver note",
        source_text=f"Source says: {value}", evidence_ids=[evidence_id],
        review_after_days=30,
    )


def test_competing_assertions_are_both_preserved_and_conflict_is_open(db: OrmSession):
    first = create_assertion(db, _assertion("person-1", "5 mg", "ev-1"))
    second = create_assertion(db, _assertion("person-1", "10 mg", "ev-2"))

    assertions = db.query(CareAssertion).filter(
        CareAssertion.person_id == "person-1",
        CareAssertion.field == "dose_reported",
    ).all()
    conflict = db.query(Contradiction).filter(
        Contradiction.person_id == "person-1",
        Contradiction.field == "dose_reported",
    ).one()
    assert len(assertions) == 2
    assert {item.asserted_value for item in assertions} == {"5 mg", "10 mg"}
    assert {item.assertion_status for item in assertions} == {"reported"}
    assert conflict.resolution_status == "unresolved"
    assert set(conflict.evidence_ids) == {"ev-1", "ev-2"}
    assert set(conflict.source_assertion_ids) == {first.id, second.id}
    assert "no assertion was selected as true" in conflict.description.lower()


def test_time_bound_observations_do_not_create_false_state_conflicts(db: OrmSession):
    first = _assertion("person-1", "calm", "ev-1").model_copy(update={
        "field": "mood", "assertion_kind": "time_bound_observation",
        "observed_at": datetime(2026, 10, 21, 9, 0, tzinfo=timezone.utc),
    })
    second = _assertion("person-1", "agitated", "ev-2").model_copy(update={
        "field": "mood", "assertion_kind": "time_bound_observation",
        "observed_at": datetime(2026, 10, 21, 16, 0, tzinfo=timezone.utc),
    })
    create_assertion(db, first)
    create_assertion(db, second)
    assert db.query(CareAssertion).filter(CareAssertion.field == "mood").count() == 2
    assert db.query(Contradiction).filter(Contradiction.field == "mood").count() == 0


def test_freshness_never_calls_unreviewed_assertion_current(db: OrmSession):
    assertion = create_assertion(db, _assertion("person-1", "5 mg", "ev-1"))
    assert get_freshness(assertion)["status"] == "needs_confirmation"

    assertion.assertion_status = "accepted"
    assertion.observed_at = datetime.now(timezone.utc) - timedelta(days=45)
    db.commit()
    result = get_freshness(assertion)
    assert result["status"] == "needs_confirmation"
    assert result["age_days"] >= 45


def test_next_best_questions_are_evidence_linked_and_idempotent(db: OrmSession):
    create_assertion(db, _assertion("person-1", "5 mg", "ev-1"))
    create_assertion(db, _assertion("person-1", "10 mg", "ev-2"))
    first = next_best_questions(db, "person-1", persist=True)
    second = next_best_questions(db, "person-1", persist=True)

    conflict_questions = [item for item in first if item["source_kind"] == "contradiction"]
    assert conflict_questions
    assert conflict_questions[0]["evidence_ids"] == ["ev-1", "ev-2"]
    assert conflict_questions[0]["persisted_question_id"]
    assert len(db.query(OpenQuestion).filter(OpenQuestion.person_id == "person-1", OpenQuestion.status == "open").all()) == len(second)
    assert "neither report has been selected as correct" in conflict_questions[0]["question"].lower()


def test_corrected_outcome_requires_correction_text():
    with pytest.raises(ValidationError):
        CareActionOutcomeCreate(
            person_id="person-1", action_id="question-1", action_type="next_question",
            outcome_status="corrected",
        )


def test_outcome_feedback_is_persisted_without_implying_resolution(db: OrmSession):
    db.add(OpenQuestion(
        id="next-question-1", person_id="person-1", question="Can you confirm this?",
        subject_type="medication", subject_id="med-1", status="open",
        priority=Priority.INFORMATIONAL, evidence_ids=["ev-1"],
    ))
    db.commit()
    payload = CareActionOutcomeCreate(
        person_id="person-1", action_id="next-question-1", action_type="next_best_question",
        outcome_status="unresolved", notes="Still waiting for confirmation",
        evidence_ids=["ev-1"],
    )
    outcome = create_action_outcome(db, payload)
    saved = db.query(CareActionOutcome).filter(CareActionOutcome.id == outcome.id).one()
    assert saved.outcome_status == "unresolved"
    assert saved.notes == "Still waiting for confirmation"
    assert saved.evidence_ids == ["ev-1"]


def test_revision_log_is_append_only(db: OrmSession):
    event = CareEvent(
        id="event-append-only", person_id="person-1", event_type="observation",
        occurred_at=datetime(2026, 10, 20, tzinfo=timezone.utc),
        title="Original", description="Original source",
        created_by_caregiver_id="caregiver-1",
    )
    db.add(event)
    db.commit()
    revision = db.query(CareStateRevision).filter_by(
        target_type="care_events", target_id="event-append-only"
    ).one()
    revision.after_state = "{}"
    with pytest.raises(ValueError, match="append-only"):
        db.flush()
    db.rollback()
