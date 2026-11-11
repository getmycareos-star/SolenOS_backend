import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.main import app  # noqa: F401 - registers the complete ORM model set
from app.models.care import Person
from app.models.uncertainty import Contradiction
from app.schemas.care_continuity import CareAssertionCreate
from app.services.care_continuity import create_assertion


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


def test_shared_assertion_ingestion_rejects_unowned_source_provenance():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = session_factory()
    try:
        person = Person(id="provenance-validation-person", name="Provenance validation test person")
        db.add(person)
        db.commit()

        with pytest.raises(HTTPException) as proposal_error:
            create_assertion(
                db,
                CareAssertionCreate(
                    person_id=person.id,
                    subject_type="person",
                    subject_id=person.id,
                    field="care_observation",
                    assertion_kind="time_bound_observation",
                    asserted_value="A caregiver report",
                    source_label="Companion caregiver report",
                    source_proposal_id="proposal-from-another-care-record",
                    created_by_caregiver_id="caregiver-a",
                ),
            )
        assert proposal_error.value.status_code == 422

        with pytest.raises(HTTPException) as message_error:
            create_assertion(
                db,
                CareAssertionCreate(
                    person_id=person.id,
                    subject_type="person",
                    subject_id=person.id,
                    field="care_observation",
                    assertion_kind="time_bound_observation",
                    asserted_value="A caregiver report",
                    source_label="Companion caregiver report",
                    source_message_id="message-without-validated-conversation",
                    created_by_caregiver_id="caregiver-a",
                ),
            )
        assert message_error.value.status_code == 422
        assert db.query(Person).filter_by(id=person.id).one()
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()
