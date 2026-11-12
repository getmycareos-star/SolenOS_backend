from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.core.change_enums import ChangeDirection, ChangeType, SignificanceVerdict
from app.models.care import CareEvent, Caregiver, Person
from app.models.change import Change
from app.models.notifications import CareNotification
from app.services.proactive_care_communications import create_meaningful_change_notifications


def test_meaningful_change_notifications_require_scoped_source_and_deduplicate():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(autocommit=False, autoflush=False, bind=engine)()
    try:
        person = Person(id="notify-person", name="Care recipient")
        other = Person(id="notify-other-person", name="Other recipient")
        db.add_all([person, other])
        db.flush()
        caregiver_one = Caregiver(
            id="notify-caregiver-one", name="Caregiver one",
            relationship="daughter", person_id=person.id,
        )
        caregiver_two = Caregiver(
            id="notify-caregiver-two", name="Caregiver two",
            relationship="son", person_id=person.id,
        )
        other_caregiver = Caregiver(
            id="notify-other-caregiver", name="Other caregiver",
            relationship="daughter", person_id=other.id,
        )
        db.add_all([caregiver_one, caregiver_two, other_caregiver])
        db.flush()

        source = CareEvent(
            id="notify-source-event",
            person_id=person.id,
            event_type="observation",
            status="recorded",
            occurred_at=datetime(2026, 11, 10, tzinfo=timezone.utc),
            title="Recorded care observation",
            description="Source record for the change.",
            created_by_caregiver_id=caregiver_one.id,
        )
        db.add(source)
        db.flush()

        meaningful = Change(
            id="notify-meaningful-change",
            person_id=person.id,
            subject_type="person",
            subject_id=person.id,
            change_type=ChangeType.EMERGENCE,
            change_direction=ChangeDirection.INCREASING,
            evidence_ids=[source.id],
            significance_verdict=SignificanceVerdict.MEANINGFUL_CANDIDATE,
            attention_candidate=True,
            is_active=True,
        )
        not_meaningful = Change(
            id="notify-not-meaningful",
            person_id=person.id,
            subject_type="person",
            subject_id=person.id,
            change_type=ChangeType.EMERGENCE,
            change_direction=ChangeDirection.UNKNOWN,
            evidence_ids=[source.id],
            significance_verdict=SignificanceVerdict.NOT_MEANINGFUL,
            attention_candidate=False,
            is_active=True,
        )
        ungrounded = Change(
            id="notify-ungrounded",
            person_id=person.id,
            subject_type="person",
            subject_id=person.id,
            change_type=ChangeType.EMERGENCE,
            change_direction=ChangeDirection.UNKNOWN,
            evidence_ids=["missing-or-foreign-source"],
            significance_verdict=SignificanceVerdict.MEANINGFUL_CANDIDATE,
            attention_candidate=True,
            is_active=True,
        )
        superseded = Change(
            id="notify-superseded",
            person_id=person.id,
            subject_type="person",
            subject_id=person.id,
            change_type=ChangeType.EMERGENCE,
            change_direction=ChangeDirection.UNKNOWN,
            evidence_ids=[source.id],
            significance_verdict=SignificanceVerdict.MEANINGFUL_CANDIDATE,
            attention_candidate=True,
            is_active=False,
            superseded_by_change_id=meaningful.id,
        )
        db.add_all([meaningful, not_meaningful, ungrounded, superseded])
        db.flush()

        now = datetime(2026, 11, 12, 8, tzinfo=timezone.utc)
        assert create_meaningful_change_notifications(db, person.id, now=now) == 2
        db.commit()
        assert create_meaningful_change_notifications(db, person.id, now=now) == 0
        db.commit()

        notifications = db.query(CareNotification).filter(
            CareNotification.person_id == person.id,
        ).all()
        assert len(notifications) == 2
        assert {item.caregiver_id for item in notifications} == {
            caregiver_one.id, caregiver_two.id,
        }
        assert {item.source_event_id for item in notifications} == {meaningful.id}
        assert all(item.category == "care_change" for item in notifications)
        assert all(item.priority == "normal" for item in notifications)
        assert all("not a diagnosis" in item.body for item in notifications)
        assert db.query(CareNotification).filter(
            CareNotification.person_id == other.id,
        ).count() == 0
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)
