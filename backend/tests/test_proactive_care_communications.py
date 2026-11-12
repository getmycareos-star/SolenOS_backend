from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.change_enums import ChangeDirection, ChangeType, SignificanceVerdict
from app.core.database import Base
from app.core.uncertainty_enums import Priority
from app.models.care import Caregiver, Evidence, Person
from app.models.change import Change
from app.models.notifications import CareNotification, CarePushDelivery, CarePushSubscription
from app.models.uncertainty import OpenQuestion
from app.services.proactive_care_communications import create_proactive_care_notifications


def test_proactive_notifications_require_person_scoped_evidence_and_explicit_question_necessity():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(autocommit=False, autoflush=False, bind=engine)()
    try:
        person = Person(id="notify-person", name="Care recipient")
        other_person = Person(id="notify-other-person", name="Other recipient")
        db.add_all([person, other_person])
        db.flush()
        caregiver_one = Caregiver(id="notify-caregiver-one", name="Caregiver one", relationship="daughter", person_id=person.id)
        caregiver_two = Caregiver(id="notify-caregiver-two", name="Caregiver two", relationship="son", person_id=person.id)
        other_caregiver = Caregiver(id="notify-other-caregiver", name="Other caregiver", relationship="daughter", person_id=other_person.id)
        db.add_all([caregiver_one, caregiver_two, other_caregiver])
        db.flush()

        evidence = Evidence(
            id="notify-evidence",
            person_id=person.id,
            type="daily_care",
            source_text="A dated caregiver report describing a change in the evening routine.",
            uploaded_by_caregiver_id=caregiver_one.id,
        )
        other_evidence = Evidence(
            id="notify-other-evidence",
            person_id=other_person.id,
            type="daily_care",
            source_text="Evidence belonging to a different care recipient.",
            uploaded_by_caregiver_id=other_caregiver.id,
        )
        db.add_all([evidence, other_evidence])
        db.flush()

        db.add_all([
            Change(
                id="notify-meaningful-change",
                person_id=person.id,
                subject_type="person",
                subject_id=person.id,
                change_type=ChangeType.EMERGENCE,
                change_direction=ChangeDirection.INCREASING,
                evidence_ids=[evidence.id],
                significance_verdict=SignificanceVerdict.MEANINGFUL_CANDIDATE,
                attention_candidate=True,
                is_active=True,
            ),
            Change(
                id="notify-ungrounded-change",
                person_id=person.id,
                subject_type="person",
                subject_id=person.id,
                change_type=ChangeType.EMERGENCE,
                change_direction=ChangeDirection.INCREASING,
                evidence_ids=["missing-evidence"],
                significance_verdict=SignificanceVerdict.MEANINGFUL_CANDIDATE,
                attention_candidate=True,
                is_active=True,
            ),
            Change(
                id="notify-inactive-change",
                person_id=person.id,
                subject_type="person",
                subject_id=person.id,
                change_type=ChangeType.EMERGENCE,
                change_direction=ChangeDirection.INCREASING,
                evidence_ids=[evidence.id],
                significance_verdict=SignificanceVerdict.MEANINGFUL_CANDIDATE,
                attention_candidate=True,
                is_active=False,
                superseded_by_change_id="notify-meaningful-change",
            ),
            OpenQuestion(
                id="notify-necessary-question",
                person_id=person.id,
                question="Which of the two reported schedules is currently being followed?",
                subject_type="person",
                subject_id=person.id,
                necessity="necessary",
                necessity_rationale="The unresolved schedule determines the next documented care handoff.",
                status="open",
                priority=Priority.HIGH,
                evidence_ids=[evidence.id],
                context=[{"decision_dependency": "The next care handoff cannot identify the active schedule without this answer."}],
            ),
            OpenQuestion(
                id="notify-invalid-necessity-question",
                person_id=person.id,
                question="Which activity preference should be used?",
                subject_type="person",
                subject_id=person.id,
                necessity="necessary",
                necessity_rationale="This would be useful context.",
                status="open",
                priority=Priority.HIGH,
                evidence_ids=[evidence.id],
                context=[],
            ),
            OpenQuestion(
                id="notify-not-assessed-question",
                person_id=person.id,
                question="What is currently known about preferred activity?",
                subject_type="person",
                subject_id=person.id,
                necessity="not_assessed",
                status="open",
                priority=Priority.CRITICAL,
                evidence_ids=[evidence.id],
            ),
            OpenQuestion(
                id="notify-cross-person-question",
                person_id=person.id,
                question="What is currently known about another person's care?",
                subject_type="person",
                subject_id=person.id,
                necessity="necessary",
                necessity_rationale="A separate recipient's care detail needs confirmation.",
                status="open",
                priority=Priority.HIGH,
                evidence_ids=[other_evidence.id],
            ),
        ])
        db.flush()

        now = datetime.now(timezone.utc)
        assert create_proactive_care_notifications(db, person.id, now=now) == 4
        db.commit()

        assert create_proactive_care_notifications(db, person.id, now=now) == 0
        db.commit()

        notifications = db.query(CareNotification).filter(CareNotification.person_id == person.id).all()
        assert len(notifications) == 4
        assert {item.caregiver_id for item in notifications} == {caregiver_one.id, caregiver_two.id}
        assert {item.category for item in notifications} == {"care_change", "care_attention"}
        assert {item.source_event_id for item in notifications} == {
            "notify-meaningful-change",
            "notify-necessary-question",
        }
        assert all(item.priority == "normal" for item in notifications)
        assert all(item.status == "active" for item in notifications)
        assert db.query(CareNotification).filter(CareNotification.person_id == other_person.id).count() == 0
        assert db.query(CareNotification).filter(
            CareNotification.source_event_id == "notify-invalid-necessity-question"
        ).count() == 0

        care_change_notification = db.query(CareNotification).filter(
            CareNotification.person_id == person.id,
            CareNotification.category == "care_change",
        ).first()
        subscription = CarePushSubscription(
            id="notify-subscription",
            person_id=person.id,
            caregiver_id=care_change_notification.caregiver_id,
            endpoint_hash="notify-endpoint-hash",
            encrypted_subscription="encrypted-test-subscription",
            created_at=now,
        )
        db.add(subscription)
        db.flush()
        delivery = CarePushDelivery(
            id="notify-pending-delivery",
            notification_id=care_change_notification.id,
            subscription_id=subscription.id,
            status="pending",
            attempt_count=0,
            next_attempt_at=now,
        )
        db.add(delivery)
        db.flush()

        meaningful_change = db.query(Change).filter_by(id="notify-meaningful-change").one()
        meaningful_change.is_active = False
        meaningful_change.superseded_by_change_id = "notify-replacement-change"
        necessary_question = db.query(OpenQuestion).filter_by(id="notify-necessary-question").one()
        necessary_question.status = "closed"
        assert create_proactive_care_notifications(db, person.id, now=now) == 0
        db.commit()

        care_change_notifications = db.query(CareNotification).filter(
            CareNotification.person_id == person.id,
            CareNotification.category == "care_change",
        ).all()
        care_attention_notifications = db.query(CareNotification).filter(
            CareNotification.person_id == person.id,
            CareNotification.category == "care_attention",
        ).all()
        assert all(item.status == "superseded" for item in care_change_notifications)
        assert all(item.status == "resolved" for item in care_attention_notifications)
        db.refresh(delivery)
        assert delivery.status == "cancelled"
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)
