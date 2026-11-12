from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.main import app  # noqa: F401 - registers the complete ORM model set
from app.models.care import Person
from app.models.uncertainty import Contradiction
from app.schemas.care_continuity import CareAssertionCreate
from app.services.care_continuity import create_assertion
from app.models.uncertainty import OpenQuestion
from app.services.change_intelligence_reconciliation import reconcile_person_change_intelligence


def test_contradiction_persists_all_source_assertion_ids():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = session_factory()
    try:
        person = Person(id="evidence-integrity-person", name="Evidence integrity test person")
        db.add(person)
        db.commit()

        first = create_assertion(
            db,
            CareAssertionCreate(
                person_id=person.id,
                subject_type="care_recipient",
                subject_id=person.id,
                field="current_location",
                asserted_value="At home",
                source_label="Caregiver report A",
                source_text="The person is at home.",
            ),
        )
        second = create_assertion(
            db,
            CareAssertionCreate(
                person_id=person.id,
                subject_type="care_recipient",
                subject_id=person.id,
                field="current_location",
                asserted_value="At hospital",
                source_label="Caregiver report B",
                source_text="The person is at the hospital.",
            ),
        )

        contradiction = (
            db.query(Contradiction)
            .filter(
                Contradiction.person_id == person.id,
                Contradiction.subject_id == person.id,
                Contradiction.field == "current_location",
                Contradiction.resolution_status == "unresolved",
            )
            .one()
        )
        assert contradiction.resolution_status == "unresolved"
        assert set(contradiction.source_assertion_ids or []) == {first.id, second.id}
        assert len(contradiction.evidence_ids or []) == 0
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()



def test_reconciliation_creates_and_refreshes_source_linked_clarification_question():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = session_factory()
    try:
        person = Person(id="uncertainty-flow-person", name="Uncertainty flow test person")
        db.add(person)
        db.commit()

        assertions = []
        for value, label in [
            ("At home", "First caregiver report"),
            ("At hospital", "Second caregiver report"),
        ]:
            assertions.append(create_assertion(
                db,
                CareAssertionCreate(
                    person_id=person.id,
                    subject_type="care_recipient",
                    subject_id=person.id,
                    field="current_location",
                    assertion_kind="current_state",
                    asserted_value=value,
                    source_label=label,
                    source_text=f"The person is {value.casefold()}.",
                ),
            ))

        reconcile_person_change_intelligence(db, person.id)
        question = db.query(OpenQuestion).filter(
            OpenQuestion.person_id == person.id,
            OpenQuestion.status == "open",
        ).one()
        original_question_id = question.id
        assert set(question.context[0]["source_ids"]) == {
            item.id for item in assertions
        }

        third = create_assertion(
            db,
            CareAssertionCreate(
                person_id=person.id,
                subject_type="care_recipient",
                subject_id=person.id,
                field="current_location",
                assertion_kind="current_state",
                asserted_value="At a clinic",
                source_label="Third caregiver report",
                source_text="The person is at a clinic.",
            ),
        )
        reconcile_person_change_intelligence(db, person.id)
        refreshed = db.query(OpenQuestion).filter(
            OpenQuestion.id == original_question_id
        ).one()
        assert refreshed.id == original_question_id
        assert set(refreshed.context[0]["source_ids"]) == {
            item.id for item in [*assertions, third]
        }
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()



def test_assertion_epistemic_state_is_separate_from_review_lifecycle():
    from datetime import datetime, timezone

    from fastapi import HTTPException
    import pytest

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(autocommit=False, autoflush=False, bind=engine)()
    try:
        person = Person(id="epistemic-state-person", name="Epistemic state test person")
        db.add(person)
        db.commit()

        reported = create_assertion(
            db,
            CareAssertionCreate(
                person_id=person.id,
                subject_type="care_recipient",
                subject_id=person.id,
                field="appetite",
                asserted_value="Eating less",
                source_label="Caregiver report",
                source_text="Caregiver says appetite has decreased.",
            ),
        )
        assert reported.epistemic_state == "reported"
        assert reported.assertion_status == "reported"

        observed = create_assertion(
            db,
            CareAssertionCreate(
                person_id=person.id,
                subject_type="care_recipient",
                subject_id=person.id,
                field="meal_intake",
                asserted_value="Left half the meal",
                source_label="Direct caregiver observation",
                source_text="Observed that half the meal remained.",
                observed_at=datetime.now(timezone.utc),
                epistemic_state="observed",
            ),
        )
        assert observed.epistemic_state == "observed"
        assert observed.assertion_status == "reported"

        with pytest.raises(HTTPException) as exc:
            create_assertion(
                db,
                CareAssertionCreate(
                    person_id=person.id,
                    subject_type="care_recipient",
                    subject_id=person.id,
                    field="diagnosis",
                    asserted_value="Verified condition",
                    source_label="Unverified input",
                    epistemic_state="verified",
                ),
            )
        assert exc.value.status_code == 422

        # Conflict and absence describe the reconciled record, not a single
        # caregiver assertion. They must be derived from competing reports or gaps.
        for derived_state in ("conflicting", "missing"):
            with pytest.raises(ValueError):
                CareAssertionCreate(
                    person_id=person.id,
                    subject_type="care_recipient",
                    subject_id=person.id,
                    field="care_status",
                    asserted_value="Not directly assertable",
                    source_label="Invalid direct state",
                    epistemic_state=derived_state,
                )
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()
