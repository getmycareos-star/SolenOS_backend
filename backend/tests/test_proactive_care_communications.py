from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.core.change_enums import ChangeDirection, ChangeType, SignificanceVerdict
from app.core.uncertainty_enums import GapLifecycleStatus, Priority
from app.models.care import CareEvent, Caregiver, Person
from app.models.change import Change
from app.models.notifications import CareNotification
from app.models.uncertainty import InformationGap, OpenQuestion
from app.services.proactive_care_communications import create_proactive_care_notifications


def test_only_grounded_changes_and_explicitly_necessary_questions_create_deduplicated_notifications():
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

        caregivers = [
            Caregiver(id="proactive-caregiver-one", name="Caregiver one", relationship="daughter", person_id=person.id),
            Caregiver(id="proactive-caregiver-two", name="Caregiver two", relationship="son", person_id=person.id),
            Caregiver(id="proactive-other-caregiver", name="Other caregiver", relationship="daughter", person_id=other_person.id),
        ]
        db.add_all(caregivers)
        db.flush()

        db.add_all([
            CareEvent(
                id="proactive-source-event",
                person_id=person.id,
                event_type="sleep_change",
                occurred_at=datetime.now(timezone.utc),
                title="Saved sleep observation",
                created_by_caregiver_id="proactive-caregiver-one",
            ),
            CareEvent(
                id="other-person-source-event",
                person_id=other_person.id,
                event_type="sleep_change",
                occurred_at=datetime.now(timezone.utc),
                title="Other person's observation",
                created_by_caregiver_id="proactive-other-caregiver",
            ),
        ])
        db.flush()

        db.add_all([
            Change(
                id="meaningful-change",
                person_id=person.id,
                subject_type="person",
                subject_id=person.id,
                change_type=ChangeType.EMERGENCE,
                change_direction=ChangeDirection.INCREASING,
                evidence_ids=["proactive-source-event"],
                significance_verdict=SignificanceVerdict.MEANINGFUL_CANDIDATE,
                attention_candidate=True,
                is_active=True,
            ),
            Change(
                id="cross-person-change",
                person_id=person.id,
                subject_type="person",
                subject_id=person.id,
                change_type=ChangeType.EMERGENCE,
                change_direction=ChangeDirection.INCREASING,
                evidence_ids=["other-person-source-event"],
                significance_verdict=SignificanceVerdict.MEANINGFUL_CANDIDATE,
                attention_candidate=True,
                is_active=True,
            ),
            Change(
                id="not-attention-worthy",
                person_id=person.id,
                subject_type="person",
                subject_id=person.id,
                change_type=ChangeType.EMERGENCE,
                change_direction=ChangeDirection.INCREASING,
                evidence_ids=["proactive-source-event"],
                significance_verdict=SignificanceVerdict.NOT_MEANINGFUL,
                attention_candidate=False,
                is_active=True,
            ),
        ])

        gap = InformationGap(
            id="necessary-source-gap",
            person_id=person.id,
            subject_type="person",
            subject_id=person.id,
            field="appointment attendance",
            priority=Priority.HIGH,
            lifecycle_status=GapLifecycleStatus.OPEN,
            description="The appointment outcome is important to the current care handoff.",
        )
        other_gap = InformationGap(
            id="other-person-gap",
            person_id=other_person.id,
            subject_type="person",
            subject_id=other_person.id,
            field="other person's appointment",
            priority=Priority.HIGH,
            lifecycle_status=GapLifecycleStatus.OPEN,
        )
        db.add_all([gap, other_gap])
        db.flush()

        db.add_all([
            OpenQuestion(
                id="necessary-question",
                person_id=person.id,
                question="Did the appointment take place?",
                subject_type="person",
                subject_id=person.id,
                necessity="necessary",
                necessity_rationale="The appointment outcome is needed to complete the active care handoff.",
                status="open",
                priority=Priority.HIGH,
                evidence_ids=[],
                context=[{"source_kind": "information_gap", "source_id": gap.id}],
            ),
            OpenQuestion(
                id="unassessed-question",
                person_id=person.id,
                question="What is the preferred activity?",
                subject_type="person",
                subject_id=person.id,
                necessity="not_assessed",
                status="open",
                priority=Priority.INFORMATIONAL,
                evidence_ids=[],
                context=[{"source_kind": "information_gap", "source_id": gap.id}],
            ),
            OpenQuestion(
                id="cross-person-question",
                person_id=person.id,
                question="Did another person's appointment take place?",
                subject_type="person",
                subject_id=person.id,
                necessity="necessary",
                necessity_rationale="Test isolation.",
                status="open",
                priority=Priority.HIGH,
                evidence_ids=[],
                context=[{"source_kind": "information_gap", "source_id": other_gap.id}],
            ),
        ])
        db.flush()

        now = datetime.now(timezone.utc)
        created = create_proactive_care_notifications(db, person.id, now=now)
        assert created == 4
        db.commit()

        repeated = create_proactive_care_notifications(db, person.id, now=now)
        db.commit()
        assert repeated == 0

        items = db.query(CareNotification).filter(CareNotification.person_id == person.id).all()
        assert len(items) == 4
        assert {item.category for item in items} == {"care_change", "care_attention"}
        assert {item.caregiver_id for item in items} == {
            "proactive-caregiver-one",
            "proactive-caregiver-two",
        }
        assert {item.source_event_id for item in items} == {
            "meaningful-change",
            "necessary-question",
        }
        assert all(item.body for item in items)
        assert db.query(CareNotification).filter(CareNotification.person_id == other_person.id).count() == 0
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)
