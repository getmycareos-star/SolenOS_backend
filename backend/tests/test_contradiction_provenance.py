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



def test_reconciliation_keeps_unassessed_conflicts_internal_without_creating_caregiver_questions():
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
        from app.services.care_continuity import next_best_questions
        candidates = next_best_questions(db, person.id, persist=False)
        conflict_candidate = next(item for item in candidates if item["source_kind"] == "contradiction")
        assert conflict_candidate["necessity"] == "not_assessed"
        assert set(conflict_candidate["source_ids"]) == {item.id for item in assertions}
        assert db.query(OpenQuestion).filter(
            OpenQuestion.person_id == person.id,
            OpenQuestion.status == "open",
        ).count() == 0

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
        refreshed_candidates = next_best_questions(db, person.id, persist=False)
        refreshed_conflict = next(item for item in refreshed_candidates if item["source_kind"] == "contradiction")
        assert set(refreshed_conflict["source_ids"]) == {item.id for item in [*assertions, third]}
        assert db.query(OpenQuestion).filter(
            OpenQuestion.person_id == person.id,
            OpenQuestion.status == "open",
        ).count() == 0
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
                    source_text="A source report states a condition, but verification is not established.",
                    epistemic_state="verified",
                ),
            )
        assert exc.value.status_code == 422

        with pytest.raises(ValueError, match="must retain a source"):
            CareAssertionCreate(
                person_id=person.id,
                subject_type="care_recipient",
                subject_id=person.id,
                field="care_status",
                asserted_value="Source-free assertion",
                source_label="Unattributed",
            )

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


def test_different_observation_times_are_qualified_not_direct_conflicts():
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.core.database import Base
    from app.main import app  # noqa: F401 - register all models
    from app.models.care import Person
    from app.models.uncertainty import Contradiction
    from app.schemas.care_continuity import CareAssertionCreate
    from app.services.care_continuity import create_assertion

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(autocommit=False, autoflush=False, bind=engine)()
    try:
        person = Person(id="temporal-context-person", name="Temporal context test")
        db.add(person)
        db.commit()

        first_time = datetime(2026, 11, 9, 8, 0, tzinfo=timezone.utc)
        second_time = first_time + timedelta(hours=3)
        first = create_assertion(
            db,
            CareAssertionCreate(
                person_id=person.id,
                subject_type="care_recipient",
                subject_id=person.id,
                field="orientation",
                asserted_value="Repeatedly asked where her husband was",
                source_label="Overnight caregiver observation",
                source_text="Observed repeated questions overnight.",
                observed_at=first_time,
                epistemic_state="observed",
            ),
        )
        second = create_assertion(
            db,
            CareAssertionCreate(
                person_id=person.id,
                subject_type="care_recipient",
                subject_id=person.id,
                field="orientation",
                asserted_value="Seemed normal during a phone call",
                source_label="Family telephone observation",
                source_text="She seemed normal during my telephone call.",
                observed_at=second_time,
                epistemic_state="observed",
            ),
        )

        conflict = db.query(Contradiction).filter_by(
            person_id=person.id,
            subject_id=person.id,
            field="orientation",
        ).one()
        assert conflict.resolution_status == "qualified"
        assert set(conflict.source_assertion_ids or []) == {first.id, second.id}
        assert first.assertion_status == "reported"
        assert second.assertion_status == "reported"
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()
