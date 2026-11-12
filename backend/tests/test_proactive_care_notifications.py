from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.core.change_enums import ChangeDirection, ChangeType, SignificanceVerdict
from app.models.care import Caregiver, Person
from app.models.change import Change
from app.models.notifications import CareNotification
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

        meaningful = Change(
            id="meaningful-change",
            person_id=person.id,
            subject_type="person",
            subject_id=person.id,
            change_type=ChangeType.EMERGENCE,
            change_direction=ChangeDirection.INCREASE,
            evidence_ids=["source-evidence-1"],
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
            change_direction=ChangeDirection.INCREASE,
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
            change_direction=ChangeDirection.INCREASE,
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
            change_direction=ChangeDirection.INCREASE,
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
