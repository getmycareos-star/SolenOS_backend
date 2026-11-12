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
from app.models.uncertainty import InformationGap
from app.services.proactive_care_communications import create_attention_change_notifications


def test_only_active_evidence_backed_meaningful_changes_create_deduplicated_in_app_items():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = session_factory()
    try:
        person = Person(id="notification-person", name="Care recipient")
        other_person = Person(id="notification-other-person", name="Other recipient")
        db.add_all([person, other_person])
        db.flush()
        caregivers = [
            Caregiver(id="caregiver-one", name="Family caregiver one", relationship="daughter", person_id=person.id),
            Caregiver(id="caregiver-two", name="Family caregiver two", relationship="son", person_id=person.id),
            Caregiver(id="other-caregiver", name="Other caregiver", relationship="daughter", person_id=other_person.id),
        ]
        db.add_all(caregivers)
        db.flush()
        db.add(CareEvent(
            id="source-event-1",
            person_id=person.id,
            event_type="sleep_change",
            occurred_at=datetime.now(timezone.utc),
            title="Saved sleep observation",
            created_by_caregiver_id="caregiver-one",
        ))
        db.flush()

        meaningful = Change(
            id="meaningful-change",
            person_id=person.id,
            subject_type="person",
            subject_id=person.id,
            change_type=ChangeType.EMERGENCE,
            change_direction=ChangeDirection.INCREASING,
            evidence_ids=["source-event-1"],
            significance_verdict=SignificanceVerdict.MEANINGFUL_CANDIDATE,
            attention_candidate=True,
            is_active=True,
        )
        no_attention = Change(
            id="no-attention-change",
            person_id=person.id,
            subject_type="person",
            subject_id=person.id,
            change_type=ChangeType.EMERGENCE,
            change_direction=ChangeDirection.INCREASING,
            evidence_ids=["source-evidence-2"],
            significance_verdict=SignificanceVerdict.NOT_MEANINGFUL,
            attention_candidate=False,
            is_active=True,
        )
        ungrounded = Change(
            id="ungrounded-change",
            person_id=person.id,
            subject_type="person",
            subject_id=person.id,
            change_type=ChangeType.EMERGENCE,
            change_direction=ChangeDirection.INCREASING,
            evidence_ids=[],
            significance_verdict=SignificanceVerdict.MEANINGFUL_CANDIDATE,
            attention_candidate=True,
            is_active=True,
        )
        superseded = Change(
            id="superseded-change",
            person_id=person.id,
            subject_type="person",
            subject_id=person.id,
            change_type=ChangeType.EMERGENCE,
            change_direction=ChangeDirection.INCREASING,
            evidence_ids=["source-evidence-3"],
            significance_verdict=SignificanceVerdict.MEANINGFUL_CANDIDATE,
            attention_candidate=True,
            is_active=False,
            superseded_by_change_id="meaningful-change",
        )
        db.add_all([meaningful, no_attention, ungrounded, superseded])
        db.flush()

        created = create_attention_change_notifications(db, person.id)
        assert created == 2
        db.commit()

        repeated = create_attention_change_notifications(db, person.id)
        db.commit()
        assert repeated == 0

        items = db.query(CareNotification).filter(CareNotification.person_id == person.id).all()
        assert len(items) == 2
        assert {item.caregiver_id for item in items} == {"caregiver-one", "caregiver-two"}
        assert {item.source_event_id for item in items} == {"meaningful-change"}
        assert all(item.category == "care_change" for item in items)
        assert all(item.priority == "normal" for item in items)
        assert all("not a diagnosis" in item.body for item in items)
        assert db.query(CareNotification).filter(CareNotification.person_id == other_person.id).count() == 0
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)


def test_only_critical_or_overdue_high_gaps_create_attention_notifications():
    from datetime import datetime, timedelta, timezone

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = session_factory()
    try:
        person = Person(id="gap-notification-person", name="Care recipient")
        caregiver = Caregiver(
            id="gap-notification-caregiver",
            name="Family caregiver",
            relationship="daughter",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.flush()
        now = datetime.now(timezone.utc)
        db.add_all([
            InformationGap(
                id="critical-gap-notification",
                person_id=person.id,
                subject_type="person",
                subject_id=person.id,
                field="critical care detail",
                priority=Priority.CRITICAL,
                lifecycle_status=GapLifecycleStatus.OPEN,
            ),
            InformationGap(
                id="overdue-gap-notification",
                person_id=person.id,
                subject_type="person",
                subject_id=person.id,
                field="time-sensitive care detail",
                priority=Priority.HIGH,
                lifecycle_status=GapLifecycleStatus.OPEN,
                expected_by=now - timedelta(minutes=5),
            ),
            InformationGap(
                id="future-gap-notification",
                person_id=person.id,
                subject_type="person",
                subject_id=person.id,
                field="future care detail",
                priority=Priority.HIGH,
                lifecycle_status=GapLifecycleStatus.OPEN,
                expected_by=now + timedelta(days=1),
            ),
            InformationGap(
                id="informational-gap-notification",
                person_id=person.id,
                subject_type="person",
                subject_id=person.id,
                field="informational detail",
                priority=Priority.INFORMATIONAL,
                lifecycle_status=GapLifecycleStatus.OPEN,
            ),
        ])
        db.flush()

        created = create_attention_change_notifications(db, person.id, now=now)
        assert created == 2
        db.commit()
        created_again = create_attention_change_notifications(db, person.id, now=now)
        db.commit()
        assert created_again == 0

        items = db.query(CareNotification).filter(
            CareNotification.person_id == person.id,
            CareNotification.category == "care_attention",
        ).all()
        assert len(items) == 2
        assert {item.source_event_id for item in items} == {
            "critical-gap-notification",
            "overdue-gap-notification",
        }
        assert all(item.priority == "high" for item in items)
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)
