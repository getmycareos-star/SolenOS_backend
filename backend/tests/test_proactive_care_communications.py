from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
import app.main  # noqa: F401 - register the complete SQLAlchemy model graph
from app.core.change_enums import ChangeDirection, ChangeType, SignificanceVerdict
from app.core.uncertainty_enums import EpistemicState, GapLifecycleStatus, GapReason, Priority
from app.models.care import Caregiver, Evidence, Person
from app.models.change import Change
from app.models.notifications import CareNotification
from app.models.uncertainty import InformationGap, OpenQuestion
from app.services.proactive_care_communications import create_proactive_care_notifications


def _assessment():
    return {
        "necessity": "necessary",
        "necessity_rationale": "The current care handoff depends on this missing detail.",
        "decision_dependency": "The active handoff cannot be completed without confirming this detail.",
    }


def test_grounded_notifications_are_deduplicated_and_retired_when_sources_change():
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

        evidence = Evidence(
            id="proactive-evidence",
            person_id=person.id,
            type="note",
            evidence_kind="caregiver_report",
            epistemic_status="caregiver_reported",
            source_text="Saved source information for a care change.",
            uploaded_by_caregiver_id="proactive-caregiver-one",
            reported_by_caregiver_id="proactive-caregiver-one",
        )
        other_evidence = Evidence(
            id="proactive-other-evidence",
            person_id=other_person.id,
            type="note",
            evidence_kind="caregiver_report",
            epistemic_status="caregiver_reported",
            source_text="This evidence belongs to another care recipient.",
            uploaded_by_caregiver_id="proactive-other-caregiver",
            reported_by_caregiver_id="proactive-other-caregiver",
        )
        db.add_all([evidence, other_evidence])
        db.flush()

        change = Change(
            id="meaningful-change",
            person_id=person.id,
            subject_type="person",
            subject_id=person.id,
            change_type=ChangeType.EMERGENCE,
            change_direction=ChangeDirection.INCREASING,
            evidence_ids=[evidence.id],
            significance_verdict=SignificanceVerdict.MEANINGFUL_CANDIDATE,
            attention_candidate=True,
            is_active=True,
        )
        cross_person_change = Change(
            id="cross-person-change",
            person_id=person.id,
            subject_type="person",
            subject_id=person.id,
            change_type=ChangeType.EMERGENCE,
            change_direction=ChangeDirection.INCREASING,
            evidence_ids=[other_evidence.id],
            significance_verdict=SignificanceVerdict.MEANINGFUL_CANDIDATE,
            attention_candidate=True,
            is_active=True,
        )
        no_attention_change = Change(
            id="not-attention-worthy",
            person_id=person.id,
            subject_type="person",
            subject_id=person.id,
            change_type=ChangeType.EMERGENCE,
            change_direction=ChangeDirection.INCREASING,
            evidence_ids=[evidence.id],
            significance_verdict=SignificanceVerdict.NOT_MEANINGFUL,
            attention_candidate=False,
            is_active=True,
        )
        gap = InformationGap(
            id="necessary-source-gap",
            person_id=person.id,
            subject_type="person",
            subject_id=person.id,
            field="appointment attendance",
            epistemic_state=EpistemicState.UNKNOWN,
            gap_reason=GapReason.NOT_DOCUMENTED,
            priority=Priority.HIGH,
            lifecycle_status=GapLifecycleStatus.OPEN,
            description="The appointment outcome is important to the current care handoff.",
            evidence_ids=[evidence.id],
        )
        other_gap = InformationGap(
            id="other-person-gap",
            person_id=other_person.id,
            subject_type="person",
            subject_id=other_person.id,
            field="other person's appointment",
            epistemic_state=EpistemicState.UNKNOWN,
            gap_reason=GapReason.NOT_DOCUMENTED,
            priority=Priority.HIGH,
            lifecycle_status=GapLifecycleStatus.OPEN,
            description="Other person's gap.",
            evidence_ids=[other_evidence.id],
        )
        question = OpenQuestion(
            id="necessary-question",
            person_id=person.id,
            question="Did the appointment take place?",
            subject_type="person",
            subject_id=person.id,
            necessity="necessary",
            necessity_rationale=_assessment()["necessity_rationale"],
            status="open",
            priority=Priority.HIGH,
            evidence_ids=[evidence.id],
            context=[{
                "source_kind": "information_gap",
                "source_id": gap.id,
                "necessity_assessment": _assessment(),
            }],
        )
        unassessed = OpenQuestion(
            id="unassessed-question",
            person_id=person.id,
            question="What activity does she prefer?",
            subject_type="person",
            subject_id=person.id,
            necessity="not_assessed",
            status="open",
            priority=Priority.INFORMATIONAL,
            evidence_ids=[evidence.id],
            context=[{"source_kind": "information_gap", "source_id": gap.id}],
        )
        cross_person_question = OpenQuestion(
            id="cross-person-question",
            person_id=person.id,
            question="Did the other person's appointment take place?",
            subject_type="person",
            subject_id=person.id,
            necessity="necessary",
            necessity_rationale="Test isolation.",
            status="open",
            priority=Priority.HIGH,
            evidence_ids=[other_evidence.id],
            context=[{
                "source_kind": "information_gap",
                "source_id": other_gap.id,
                "necessity_assessment": _assessment(),
            }],
        )
        db.add_all([
            change, cross_person_change, no_attention_change,
            gap, other_gap, question, unassessed, cross_person_question,
        ])
        db.commit()

        now = datetime.now(timezone.utc)
        created = create_proactive_care_notifications(db, person.id, now=now)
        db.commit()
        assert created == 4

        # Repeated reconciliation does not create duplicate caregiver messages.
        assert create_proactive_care_notifications(db, person.id, now=now) == 0
        db.commit()

        items = db.query(CareNotification).filter(CareNotification.person_id == person.id).all()
        assert len(items) == 4
        assert {item.category for item in items} == {"care_change", "care_attention"}
        assert {item.caregiver_id for item in items} == {
            "proactive-caregiver-one",
            "proactive-caregiver-two",
        }
        assert {item.source_event_id for item in items} == {"meaningful-change", "necessary-question"}

        # When the question is no longer necessary, its old inbox items are
        # converted into non-actionable updates instead of remaining stale tasks.
        question.status = "closed"
        question.necessity = "not_assessed"
        question.closed_reason = "automatically_suppressed: no current decision depends on this detail"
        db.commit()
        assert create_proactive_care_notifications(db, person.id, now=now) == 0
        db.commit()
        updated_questions = db.query(CareNotification).filter(
            CareNotification.person_id == person.id,
            CareNotification.source_event_id == question.id,
        ).all()
        assert len(updated_questions) == 2
        assert all(item.category == "care_attention_updated" for item in updated_questions)

        # A superseded change is similarly retired. Reopening the necessary
        # question creates a fresh attention item rather than reusing stale copy.
        change.is_active = False
        db.commit()
        assert create_proactive_care_notifications(db, person.id, now=now) == 0
        db.commit()
        updated_changes = db.query(CareNotification).filter(
            CareNotification.person_id == person.id,
            CareNotification.source_event_id == change.id,
        ).all()
        assert len(updated_changes) == 2
        assert all(item.category == "care_change_superseded" for item in updated_changes)

        question.status = "open"
        question.necessity = "necessary"
        question.necessity_rationale = _assessment()["necessity_rationale"]
        question.context = [{
            "source_kind": "information_gap",
            "source_id": gap.id,
            "necessity_assessment": _assessment(),
        }]
        db.commit()
        assert create_proactive_care_notifications(db, person.id, now=now) == 2
        db.commit()
        active_questions = db.query(CareNotification).filter(
            CareNotification.person_id == person.id,
            CareNotification.source_event_id == question.id,
            CareNotification.category == "care_attention",
        ).all()
        assert len(active_questions) == 2
        assert all("not a diagnosis" not in item.body for item in active_questions)
        assert db.query(CareNotification).filter(CareNotification.person_id == other_person.id).count() == 0
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)
