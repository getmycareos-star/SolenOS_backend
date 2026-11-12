from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.core.change_enums import ChangeDirection, ChangeType, SignificanceVerdict
from app.models.care import CareEvent, Caregiver, Evidence, Person
from app.models.change import Change
from app.models.notifications import CareNotification
from app.models.uncertainty import OpenQuestion
from app.services.change_intelligence_reconciliation import create_proactive_inbox_items
from app.api.collaboration import _notification_lifecycle_status


def test_only_grounded_meaningful_changes_and_necessary_questions_create_inbox_items():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = session_factory()
    try:
        person = Person(id="proactive-person", name="Care recipient")
        other_person = Person(id="proactive-other-person", name="Other recipient")
        db.add_all([person, other_person])
        db.flush()
        caregiver_one = Caregiver(
            id="proactive-caregiver-one", name="Caregiver one",
            relationship="daughter", person_id=person.id,
        )
        caregiver_two = Caregiver(
            id="proactive-caregiver-two", name="Caregiver two",
            relationship="son", person_id=person.id,
        )
        other_caregiver = Caregiver(
            id="proactive-other-caregiver", name="Other caregiver",
            relationship="daughter", person_id=other_person.id,
        )
        db.add_all([caregiver_one, caregiver_two, other_caregiver])
        db.flush()

        now = datetime.now(timezone.utc)
        event = CareEvent(
            id="proactive-source-event",
            person_id=person.id,
            event_type="daily_care",
            status="recorded",
            occurred_at=now - timedelta(days=1),
            title="Saved care entry",
            description="Caregiver-reported source event.",
            evidence_ids=[],
            created_by_caregiver_id=caregiver_one.id,
        )
        db.add(event)
        evidence = Evidence(
            id="proactive-source-evidence",
            person_id=person.id,
            type="caregiver_report",
            source_text="Caregiver report supporting a necessary care question.",
            uploaded_by_caregiver_id=caregiver_one.id,
        )
        db.add(evidence)
        db.flush()

        meaningful = Change(
            id="proactive-meaningful-change",
            person_id=person.id,
            subject_type="person",
            subject_id=person.id,
            change_type=ChangeType.EMERGENCE,
            change_direction=ChangeDirection.INCREASING,
            evidence_ids=[event.id],
            significance_verdict=SignificanceVerdict.MEANINGFUL_CANDIDATE,
            attention_candidate=True,
            is_active=True,
            comparison_window_start=now - timedelta(days=7),
            comparison_window_end=now,
        )
        insignificant = Change(
            id="proactive-insignificant-change",
            person_id=person.id,
            subject_type="person",
            subject_id=person.id,
            change_type=ChangeType.EMERGENCE,
            change_direction=ChangeDirection.INCREASING,
            evidence_ids=[event.id],
            significance_verdict=SignificanceVerdict.NOT_MEANINGFUL,
            attention_candidate=False,
            is_active=True,
        )
        ungrounded = Change(
            id="proactive-ungrounded-change",
            person_id=person.id,
            subject_type="person",
            subject_id=person.id,
            change_type=ChangeType.EMERGENCE,
            change_direction=ChangeDirection.INCREASING,
            evidence_ids=["missing-event"],
            significance_verdict=SignificanceVerdict.MEANINGFUL_CANDIDATE,
            attention_candidate=True,
            is_active=True,
        )
        db.add_all([meaningful, insignificant, ungrounded])
        db.add_all([
            OpenQuestion(
                id="proactive-necessary-question",
                person_id=person.id,
                question="Which schedule is currently being followed?",
                subject_type="person",
                subject_id=person.id,
                necessity="necessary",
                necessity_rationale="The next care handoff depends on the current schedule.",
                status="open",
                evidence_ids=[evidence.id],
            ),
            OpenQuestion(
                id="proactive-not-assessed-question",
                person_id=person.id,
                question="What is preferred?",
                subject_type="person",
                subject_id=person.id,
                necessity="not_assessed",
                status="open",
                evidence_ids=[evidence.id],
            ),
            OpenQuestion(
                id="proactive-ungrounded-question",
                person_id=person.id,
                question="Which detail is correct?",
                subject_type="person",
                subject_id=person.id,
                necessity="necessary",
                necessity_rationale="A current decision depends on the detail.",
                status="open",
                evidence_ids=["missing-evidence"],
            ),
        ])
        db.flush()

        created = create_proactive_inbox_items(db, person.id, now=now)
        assert created == 4
        db.commit()

        repeated = create_proactive_inbox_items(db, person.id, now=now)
        db.commit()
        assert repeated == 0

        items = db.query(CareNotification).filter(CareNotification.person_id == person.id).all()
        assert len(items) == 4
        assert {item.caregiver_id for item in items} == {
            caregiver_one.id, caregiver_two.id,
        }
        assert {item.category for item in items} == {"care_change", "care_attention"}
        assert db.query(CareNotification).filter(
            CareNotification.person_id == other_person.id
        ).count() == 0

        change_items = [item for item in items if item.category == "care_change"]
        question_items = [item for item in items if item.category == "care_attention"]
        assert len(change_items) == 2
        assert len(question_items) == 2
        assert all("not a diagnosis" in item.body for item in change_items)
        assert all("If you are not sure" in item.body for item in question_items)

        meaningful.is_active = False
        meaningful.superseded_by_change_id = "newer-change"
        necessary = db.query(OpenQuestion).filter_by(id="proactive-necessary-question").one()
        necessary.status = "closed"
        db.flush()
        assert _notification_lifecycle_status(db, change_items[0]) == "superseded"
        assert _notification_lifecycle_status(db, question_items[0]) == "resolved"
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)
