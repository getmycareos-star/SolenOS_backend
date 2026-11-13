from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.change_enums import ChangeDirection, ChangeType, SignificanceVerdict
from app.core.database import Base
from app.core.uncertainty_enums import Priority
from app.models.care import Caregiver, Evidence, Person
from app.models.change import Change
from app.models.notifications import CareNotification, CarePushDelivery, CarePushSubscription
from app.models.open_loop import OpenLoopRecord
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
            occurred_at=datetime(2026, 10, 6, tzinfo=timezone.utc),
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
                subject_type="sleep",
                subject_id="sleep-pattern",
                change_type=ChangeType.EMERGENCE,
                change_direction=ChangeDirection.INCREASING,
                comparison_window_start=datetime(2026, 10, 1, tzinfo=timezone.utc),
                comparison_window_end=datetime(2026, 10, 7, tzinfo=timezone.utc),
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
        due_soon = now + timedelta(hours=4)
        due_later = now + timedelta(hours=18)

        def open_loop(loop_id, caregiver_id, deadline, deadline_source="explicit_user", status="open", loop_type="follow_up", outcome_status="outcome_unknown"):
            return OpenLoopRecord(
                id=loop_id,
                person_id=person.id,
                caregiver_id=caregiver_id,
                loop_type=loop_type,
                title=f"Follow-up {loop_id}",
                description=f"Recorded follow-up {loop_id}",
                status=status,
                outcome_status=outcome_status,
                owner_role="unassigned",
                source_type="appointment_workspace_follow_up",
                source_id=f"workspace:{loop_id}",
                source_fingerprint=("a" * 64),
                evidence=[{"record_type": "appointment_workspace", "record_id": "workspace-1"}],
                uncertainty="reported",
                deadline_at=deadline,
                deadline_source=deadline_source,
                priority="normal",
                priority_reasons=[],
                version=1,
            )

        due_loop = open_loop("notify-due-loop", caregiver_one.id, due_soon)
        later_loop = open_loop(
            "notify-later-loop", caregiver_two.id, due_later,
            loop_type="awaiting_response",
        )
        outside_window_loop = open_loop(
            "notify-outside-window-loop", caregiver_one.id, now + timedelta(hours=48),
        )
        policy_deadline_loop = open_loop(
            "notify-policy-deadline-loop", caregiver_one.id, now + timedelta(hours=2),
            deadline_source="system_policy",
        )
        deferred_loop = open_loop(
            "notify-deferred-loop", caregiver_one.id, now + timedelta(hours=2),
            status="deferred",
        )
        stale_overdue_loop = open_loop(
            "notify-stale-overdue-loop", caregiver_one.id, now - timedelta(hours=48),
        )
        reported_loop = open_loop(
            "notify-reported-loop", caregiver_one.id, now + timedelta(hours=2),
            outcome_status="outcome_reported",
        )
        verified_loop = open_loop(
            "notify-verified-loop", caregiver_one.id, now + timedelta(hours=2),
            outcome_status="outcome_verified",
        )
        closure_verification_loop = open_loop(
            "notify-closure-verification-loop", caregiver_one.id, now + timedelta(hours=2),
            status="pending_closure_verification", outcome_status="outcome_reported",
        )
        db.add_all([
            due_loop, later_loop, outside_window_loop,
            policy_deadline_loop, deferred_loop, stale_overdue_loop,
            reported_loop, verified_loop, closure_verification_loop,
        ])
        db.flush()

        due_loop_source = f"{due_loop.id}|{due_soon.isoformat()}"
        later_loop_source = f"{later_loop.id}|{due_later.isoformat()}"
        assert create_proactive_care_notifications(db, person.id, now=now) == 6
        db.commit()

        assert create_proactive_care_notifications(db, person.id, now=now) == 0
        db.commit()

        notifications = db.query(CareNotification).filter(CareNotification.person_id == person.id).all()
        assert len(notifications) == 6
        assert {item.caregiver_id for item in notifications} == {caregiver_one.id, caregiver_two.id}
        assert {item.category for item in notifications} == {"care_change", "care_attention", "open_loop_due"}
        assert {item.source_event_id for item in notifications} == {
            "notify-meaningful-change",
            "notify-necessary-question",
            due_loop_source,
            later_loop_source,
        }
        assert all(
            item.priority == "normal"
            for item in notifications
            if item.category in {"care_change", "care_attention"}
        )
        open_loop_notifications = [item for item in notifications if item.category == "open_loop_due"]
        assert len(open_loop_notifications) == 2
        assert {item.caregiver_id for item in open_loop_notifications} == {caregiver_one.id, caregiver_two.id}
        assert {item.priority for item in open_loop_notifications} == {"high", "normal"}
        assert all(item.status == "active" for item in notifications)
        care_change_notification = next(item for item in notifications if item.category == "care_change")
        care_attention_notification = next(item for item in notifications if item.category == "care_attention")
        assert "possible change in sleep" in care_change_notification.body
        assert "2026-10-01 to 2026-10-07" in care_change_notification.body
        assert "Daily Care entry (2026-10-06)" in care_change_notification.body
        assert "Daily Care entry (2026-10-06)" in care_attention_notification.body
        assert "If you're not sure, you can say so" in care_attention_notification.body
        assert db.query(CareNotification).filter(
            CareNotification.source_event_id.like("notify-outside-window-loop|%")
        ).count() == 0
        assert db.query(CareNotification).filter(
            CareNotification.source_event_id.like("notify-policy-deadline-loop|%")
        ).count() == 0
        assert db.query(CareNotification).filter(
            CareNotification.source_event_id.like("notify-deferred-loop|%")
        ).count() == 0
        assert db.query(CareNotification).filter(
            CareNotification.source_event_id.like("notify-stale-overdue-loop|%")
        ).count() == 0
        for suppressed_loop_id in (
            "notify-reported-loop",
            "notify-verified-loop",
            "notify-closure-verification-loop",
        ):
            assert db.query(CareNotification).filter(
                CareNotification.source_event_id.like(f"{suppressed_loop_id}|%")
            ).count() == 0
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

        due_loop_notification = db.query(CareNotification).filter(
            CareNotification.person_id == person.id,
            CareNotification.category == "open_loop_due",
            CareNotification.source_event_id == due_loop_source,
        ).one()
        due_subscription = CarePushSubscription(
            id="notify-due-subscription",
            person_id=person.id,
            caregiver_id=caregiver_one.id,
            endpoint_hash="notify-due-endpoint-hash",
            encrypted_subscription="encrypted-due-test-subscription",
            created_at=now,
        )
        db.add(due_subscription)
        db.flush()
        due_delivery = CarePushDelivery(
            id="notify-due-pending-delivery",
            notification_id=due_loop_notification.id,
            subscription_id=due_subscription.id,
            status="pending",
            attempt_count=0,
            next_attempt_at=now,
        )
        db.add(due_delivery)
        db.flush()

        due_loop.status = "closed"
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
        db.refresh(due_loop_notification)
        assert due_loop_notification.status == "resolved"
        db.refresh(delivery)
        db.refresh(due_delivery)
        assert delivery.status == "cancelled"
        assert due_delivery.status == "cancelled"
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)

def test_proactive_question_notifications_preserve_critical_and_limit_ordinary():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(autocommit=False, autoflush=False, bind=engine)()
    try:
        person = Person(id="notify-limit-person", name="Care recipient")
        caregiver = Caregiver(
            id="notify-limit-caregiver", name="Caregiver",
            relationship="daughter", person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.flush()
        evidence = Evidence(
            id="notify-limit-evidence", person_id=person.id,
            type="caregiver_report",
            source_text="A caregiver report linked to the current care decision.",
            uploaded_by_caregiver_id=caregiver.id,
        )
        db.add(evidence)
        db.flush()

        cases = [
            ("notify-critical", Priority.CRITICAL),
            ("notify-high", Priority.HIGH),
            ("notify-medium", Priority.MEDIUM),
            ("notify-low", Priority.LOW),
        ]
        for question_id, priority in cases:
            rationale = f"The next care decision depends on the answer to {question_id}."
            db.add(OpenQuestion(
                id=question_id, person_id=person.id,
                question=f"What should the care team know about {question_id}?",
                subject_type="care", subject_id="current-plan",
                status="open", necessity="necessary",
                necessity_rationale=rationale, priority=priority,
                intended_answerer_role="professional" if question_id == "notify-critical" else "caregiver",
                intended_answerer_label="prescribing clinician" if question_id == "notify-critical" else None,
                evidence_ids=[evidence.id],
                context=[{
                    "source_kind": "caregiver_report",
                    "source_id": evidence.id,
                    "necessity_assessment": {
                        "necessity_rationale": rationale,
                        "decision_dependency": f"The current handoff cannot be completed accurately without {question_id}.",
                        "evidence_ids": [evidence.id],
                    },
                }],
            ))
        db.commit()

        created = create_proactive_care_notifications(
            db, person.id, now=datetime(2026, 11, 13, tzinfo=timezone.utc)
        )
        db.commit()
        notifications = db.query(CareNotification).filter(
            CareNotification.person_id == person.id,
            CareNotification.category == "care_attention",
        ).all()
        assert created == 3
        assert {row.source_event_id for row in notifications} == {
            "notify-critical", "notify-high", "notify-medium",
        }
        assert "notify-low" not in {row.source_event_id for row in notifications}
        professional_notification = next(
            row for row in notifications if row.source_event_id == "notify-critical"
        )
        assert professional_notification.title == "A care detail needs care-team confirmation"
        assert "Best person to ask: prescribing clinician" in professional_notification.body
        assert "If you're not sure, you can say so." not in professional_notification.body

        new_rationale = "The next transfer decision depends on confirming this additional support detail."
        db.add(OpenQuestion(
            id="notify-new-high", person_id=person.id,
            question="Has the person needed another form of support during transfers?",
            subject_type="care", subject_id="current-plan",
            status="open", necessity="necessary",
            necessity_rationale=new_rationale, priority=Priority.HIGH,
            evidence_ids=[evidence.id],
            context=[{
                "source_kind": "caregiver_report",
                "source_id": evidence.id,
                "necessity_assessment": {
                    "necessity_rationale": new_rationale,
                    "decision_dependency": "The transfer handoff cannot describe the current support needs without this detail.",
                    "evidence_ids": [evidence.id],
                },
            }],
        ))
        db.commit()
        created_again = create_proactive_care_notifications(
            db, person.id, now=datetime(2026, 11, 13, 1, tzinfo=timezone.utc)
        )
        db.commit()
        active_notifications = db.query(CareNotification).filter(
            CareNotification.person_id == person.id,
            CareNotification.category == "care_attention",
            CareNotification.status == "active",
        ).all()
        assert created_again == 1
        assert {row.source_event_id for row in active_notifications} == {
            "notify-critical", "notify-high", "notify-new-high",
        }
        medium_notification = db.query(CareNotification).filter(
            CareNotification.person_id == person.id,
            CareNotification.source_event_id == "notify-medium",
        ).one()
        assert medium_notification.status == "resolved"
        assert medium_notification.resolution_reason == "question_not_currently_prioritized"
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()

