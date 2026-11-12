from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models.care import Caregiver, Person
from app.models.uncertainty import OpenQuestion
from app.models.care_continuity import CareStateRevision
from app.services.care_continuity import _record_open_question_revision, _open_question_state


def test_explicit_question_revision_suppresses_duplicate_generic_orm_revision():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(autocommit=False, autoflush=False, bind=engine)()
    try:
        person = Person(id="question-revision-person", name="Care recipient")
        db.add(person)
        db.flush()
        caregiver = Caregiver(
            id="question-revision-caregiver",
            name="Family caregiver",
            relationship="daughter",
            person_id=person.id,
        )
        db.add(caregiver)
        db.flush()

        question = OpenQuestion(
            id="question-revision-target",
            person_id=person.id,
            question="Who observed the change?",
            subject_type="routine",
            subject_id="evening-routine",
            necessity="necessary",
            necessity_rationale="The source reports conflict and this detail affects the handoff.",
            status="open",
            created_by_caregiver_id=caregiver.id,
        )
        db.add(question)
        _record_open_question_revision(
            db,
            question,
            before_state=None,
            change_kind="created",
            actor="caregiver_question_creation",
            changed_by_caregiver_id=caregiver.id,
        )
        db.commit()

        first = db.query(CareStateRevision).filter_by(
            target_type="open_questions",
            target_id=question.id,
        ).order_by(CareStateRevision.revision_number.asc()).all()
        assert len(first) == 1
        assert first[0].revision_number == 1
        assert '"lifecycle_actor": "caregiver_question_creation"' in first[0].after_state

        before = _open_question_state(question)
        question.status = "answered"
        question.answer = "I observed it."
        question.answer_provenance = "Caregiver report."
        _record_open_question_revision(
            db,
            question,
            before_state=before,
            change_kind="updated",
            actor="caregiver_question_update",
            changed_by_caregiver_id=caregiver.id,
        )
        db.commit()

        revisions = db.query(CareStateRevision).filter_by(
            target_type="open_questions",
            target_id=question.id,
        ).order_by(CareStateRevision.revision_number.asc()).all()
        assert [item.revision_number for item in revisions] == [1, 2]
        assert len(revisions) == 2
        assert '"lifecycle_actor": "caregiver_question_update"' in revisions[1].after_state
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)
