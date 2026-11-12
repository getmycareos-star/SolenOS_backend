from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.core.uncertainty_enums import GapLifecycleStatus, Priority
from app.models.care import Person
from app.models.uncertainty import InformationGap, OpenQuestion
from app.services.care_continuity import next_best_questions


def test_only_critical_or_overdue_high_priority_gaps_become_caregiver_questions():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = session_factory()
    try:
        person = Person(id="necessity-person", name="Care recipient")
        db.add(person)
        now = datetime.now(timezone.utc)
        gaps = [
            InformationGap(
                id="critical-gap",
                person_id=person.id,
                subject_type="person",
                subject_id=person.id,
                field="current medication status",
                priority=Priority.CRITICAL,
                lifecycle_status=GapLifecycleStatus.OPEN,
                description="This detail is explicitly marked critical by the care record.",
            ),
            InformationGap(
                id="overdue-high-gap",
                person_id=person.id,
                subject_type="person",
                subject_id=person.id,
                field="appointment attendance",
                priority=Priority.HIGH,
                lifecycle_status=GapLifecycleStatus.OPEN,
                expected_by=now - timedelta(hours=2),
                description="The high-priority confirmation deadline has passed.",
            ),
            InformationGap(
                id="future-high-gap",
                person_id=person.id,
                subject_type="person",
                subject_id=person.id,
                field="upcoming appointment details",
                priority=Priority.HIGH,
                lifecycle_status=GapLifecycleStatus.OPEN,
                expected_by=now + timedelta(days=1),
                description="The confirmation deadline is still in the future.",
            ),
            InformationGap(
                id="informational-gap",
                person_id=person.id,
                subject_type="person",
                subject_id=person.id,
                field="preferred activity",
                priority=Priority.INFORMATIONAL,
                lifecycle_status=GapLifecycleStatus.OPEN,
                description="This is useful context but not a caregiver task.",
            ),
        ]
        db.add_all(gaps)
        db.flush()

        output = next_best_questions(db, person.id, persist=True, commit=False)
        questions = db.query(OpenQuestion).filter(OpenQuestion.person_id == person.id).all()
        by_field = {question.question: question for question in questions}

        assert len(output) == 4
        assert len(questions) == 4
        critical = by_field["What is currently known about current medication status?"]
        overdue = by_field["What is currently known about appointment attendance?"]
        future = by_field["What is currently known about upcoming appointment details?"]
        informational = by_field["What is currently known about preferred activity?"]

        assert critical.necessity == "necessary"
        assert critical.necessity_rationale == gaps[0].description
        assert overdue.necessity == "necessary"
        assert overdue.necessity_rationale == gaps[1].description
        assert future.necessity == "not_assessed"
        assert future.necessity_rationale is None
        assert informational.necessity == "not_assessed"
        assert informational.necessity_rationale is None
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)
