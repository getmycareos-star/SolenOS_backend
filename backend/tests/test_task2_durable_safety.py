from datetime import datetime, timezone
import hashlib

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.exc import SQLAlchemyError

from app.core.database import Base
from app.models.care import Person, Caregiver, Evidence
from app.models.action import Action as ActionModel
from app.services.action_lifecycle import ActionLifecycleService
from app.schemas.action import ActionCompletionEvidence
from app.models.reminder import CareReminder, ReminderDelivery, ReminderDeliveryAttempt
from app.models.medication_authority import MedicationAuthorityVerification
from app.services.reminder_scheduler import (
    materialize_due_reminders,
    deliver_pending_receipts,
    next_local_occurrence,
)
from app.services.medication_authority import get_verified_medication_authority
from app.schemas.reminder import ReminderCreate
from pydantic import ValidationError


def make_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return engine, sessionmaker(autocommit=False, autoflush=False, bind=engine)()


def seed_action(db):
    person = Person(id="p-task2", name="Task 2 Test")
    caregiver = Caregiver(
        id="c-task2", name="Test Caregiver", email="task2@example.com",
        relationship="Family", person_id=person.id, timezone="America/New_York",
    )
    evidence = Evidence(
        id="e-task2", person_id=person.id, type="document",
        source_text="Care plan: confirm the follow-up appointment on the scheduled date.",
        uploaded_by_caregiver_id=caregiver.id, evidence_status="uploaded",
    )
    action = ActionModel(
        id="a-task2", person_id=person.id, source_evidence_id=evidence.id,
        source_passage="confirm the follow-up appointment on the scheduled date",
        normalized_action="Confirm the follow-up appointment",
        original_text="Confirm the follow-up appointment on the scheduled date",
        created_by_caregiver_id=caregiver.id, status="active",
    )
    db.add_all([person, caregiver, evidence, action])
    db.commit()
    return person, caregiver, evidence, action


def test_daily_recurrence_preserves_local_wall_clock_across_dst():
    current = datetime(2026, 3, 7, 14, 30, tzinfo=timezone.utc)  # 09:30 EST
    next_run = next_local_occurrence(current, "America/New_York", "09:30", "daily")
    assert next_run == datetime(2026, 3, 8, 13, 30, tzinfo=timezone.utc)  # 09:30 EDT


def test_nonexistent_dst_wall_time_moves_to_first_valid_minute():
    current = datetime(2026, 3, 7, 7, 30, tzinfo=timezone.utc)
    next_run = next_local_occurrence(current, "America/New_York", "02:30", "daily")
    assert next_run.astimezone(__import__("zoneinfo").ZoneInfo("America/New_York")).strftime("%H:%M") == "03:00"


def test_due_reminder_creates_one_persistent_idempotent_delivery_receipt():
    engine, db = make_db()
    try:
        person, caregiver, evidence, action = seed_action(db)
        due = datetime(2026, 10, 23, 10, 0, tzinfo=timezone.utc)
        reminder = CareReminder(
            id="r-task2", person_id=person.id, action_id=action.id,
            created_by_caregiver_id=caregiver.id, title="Follow-up",
            message="Confirm the appointment", timezone="UTC", recurrence="once",
            next_run_at=due, local_time="10:00", enabled=True,
        )
        db.add(reminder)
        db.commit()

        assert materialize_due_reminders(db, datetime(2026, 10, 23, 11, 0, tzinfo=timezone.utc)) == 1
        assert materialize_due_reminders(db, datetime(2026, 10, 23, 11, 1, tzinfo=timezone.utc)) == 0
        receipt = db.query(ReminderDelivery).filter_by(reminder_id=reminder.id).one()
        assert receipt.idempotency_key
        assert receipt.status == "pending"
        assert deliver_pending_receipts(db, datetime(2026, 10, 23, 11, 2, tzinfo=timezone.utc)) == 1
        db.refresh(receipt)
        assert receipt.status == "delivered"
        assert receipt.delivered_at is not None
        assert receipt.attempt_count == 1
        attempt = db.query(ReminderDeliveryAttempt).filter_by(delivery_id=receipt.id).one()
        assert attempt.attempt_number == 1
        assert attempt.outcome == "delivered"
        assert attempt.finished_at is not None
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_medication_action_requires_separate_matching_source_verification():
    engine, db = make_db()
    try:
        person, caregiver, evidence, action = seed_action(db)
        evidence.source_text = "Prescriber order: take medication according to attached directions."
        action.source_passage = "take medication according to attached directions"
        action.original_text = "Take medication according to attached directions"
        action.normalized_action = "Take medication according to verified prescriber directions"
        action.is_medication_action = True
        db.commit()
        assert get_verified_medication_authority(
            db, person_id=person.id, evidence_id=evidence.id, source_authority="physician"
        ) is None

        verification = MedicationAuthorityVerification(
            id="v-task2", evidence_id=evidence.id, person_id=person.id,
            source_text_sha256=hashlib.sha256(evidence.source_text.encode("utf-8")).hexdigest(),
            authority_role="physician", verifier_identity="Dr Test",
            verification_method="signed_prescription",
            verified_by_caregiver_id=caregiver.id,
            verified_at=datetime.now(timezone.utc),
        )
        db.add(verification)
        db.commit()
        assert get_verified_medication_authority(
            db, person_id=person.id, evidence_id=evidence.id, source_authority="physician"
        ) is not None

        evidence.source_text = "Changed after verification"
        db.commit()
        assert get_verified_medication_authority(
            db, person_id=person.id, evidence_id=evidence.id, source_authority="physician"
        ) is None
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_failed_delivery_attempt_is_persisted_and_retried():
    engine, db = make_db()
    try:
        person, caregiver, evidence, action = seed_action(db)
        due = datetime(2026, 10, 23, 10, 0, tzinfo=timezone.utc)
        reminder = CareReminder(
            id="r-retry", person_id=person.id, action_id=action.id,
            created_by_caregiver_id=caregiver.id, title="Retry",
            message="Retry delivery", timezone="UTC", recurrence="once",
            next_run_at=due, local_time="10:00", enabled=True,
            max_attempts=3, retry_delay_seconds=10,
        )
        db.add(reminder)
        db.commit()
        materialize_due_reminders(db, datetime(2026, 10, 23, 11, 0, tzinfo=timezone.utc))
        original_commit = db.commit
        calls = {"count": 0}

        def fail_once():
            calls["count"] += 1
            if calls["count"] == 1:
                raise SQLAlchemyError("simulated transient failure")
            original_commit()

        db.commit = fail_once
        assert deliver_pending_receipts(db, datetime(2026, 10, 23, 11, 1, tzinfo=timezone.utc)) == 0
        db.commit = original_commit
        receipt = db.query(ReminderDelivery).filter_by(reminder_id=reminder.id).one()
        assert receipt.status == "retry"
        attempt_one = db.query(ReminderDeliveryAttempt).filter_by(delivery_id=receipt.id, attempt_number=1).one()
        assert attempt_one.outcome == "retry"
        retry_time = receipt.next_attempt_at
        assert deliver_pending_receipts(db, retry_time + __import__("datetime").timedelta(seconds=1)) == 1
        db.refresh(receipt)
        assert receipt.status == "delivered"
        attempts = db.query(ReminderDeliveryAttempt).filter_by(delivery_id=receipt.id).order_by(ReminderDeliveryAttempt.attempt_number).all()
        assert [item.outcome for item in attempts] == ["retry", "delivered"]
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()



def test_action_completion_persists_timestamp_and_caregiver_attribution():
    engine, db = make_db()
    try:
        person, caregiver, evidence, action = seed_action(db)
        result = ActionLifecycleService().record_completion(
            action,
            ActionCompletionEvidence(evidence_ids=[evidence.id], completion_confidence=0.9),
            db,
            completed_by_caregiver_id=caregiver.id,
        )
        assert result.status == "completed"
        assert result.completed_at is not None
        assert result.completed_by_caregiver_id == caregiver.id
        assert result.completed_by_caregiver_label == caregiver.name
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()



def test_terminal_action_suppresses_due_reminders():
    engine, db = make_db()
    try:
        person, caregiver, evidence, action = seed_action(db)
        action.status = "completed"
        reminder = CareReminder(
            id="r-terminal", person_id=person.id, action_id=action.id,
            created_by_caregiver_id=caregiver.id, title="No longer needed",
            message="This must not be delivered", timezone="UTC", recurrence="daily",
            next_run_at=datetime(2026, 10, 23, 10, 0, tzinfo=timezone.utc),
            local_time="10:00", enabled=True,
        )
        db.add(reminder)
        db.commit()
        assert materialize_due_reminders(db, datetime(2026, 10, 23, 11, 0, tzinfo=timezone.utc)) == 0
        db.refresh(reminder)
        assert reminder.enabled is False
        assert db.query(ReminderDelivery).filter_by(reminder_id=reminder.id).count() == 0
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()



def test_medication_reminder_is_suppressed_without_current_source_authority():
    engine, db = make_db()
    try:
        person, caregiver, evidence, action = seed_action(db)
        evidence.source_text = "Prescriber instruction: take medication 5 mg daily."
        action.source_passage = "Prescriber instruction: take medication 5 mg daily."
        action.original_text = "Prescriber instruction: take medication 5 mg daily."
        action.normalized_action = "Take medication 5 mg daily"
        action.is_medication_action = True
        due = datetime(2026, 10, 23, 10, 0, tzinfo=timezone.utc)
        reminder = CareReminder(
            id="r-med-no-authority", person_id=person.id, action_id=action.id,
            created_by_caregiver_id=caregiver.id, title="Medication reminder",
            message="Medication reminder must be held until verified", timezone="UTC",
            recurrence="once", next_run_at=due, local_time="10:00", enabled=True,
        )
        db.add(reminder)
        db.commit()

        assert materialize_due_reminders(db, datetime(2026, 10, 23, 11, 0, tzinfo=timezone.utc)) == 0
        db.refresh(reminder)
        assert reminder.enabled is False
        assert db.query(ReminderDelivery).filter_by(reminder_id=reminder.id).count() == 0
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_medication_authority_expiry_cancels_already_materialized_delivery():
    engine, db = make_db()
    try:
        person, caregiver, evidence, action = seed_action(db)
        evidence.source_text = "Prescriber instruction: take medication 5 mg daily."
        action.source_passage = "Prescriber instruction: take medication 5 mg daily."
        action.original_text = "Prescriber instruction: take medication 5 mg daily."
        action.normalized_action = "Take medication 5 mg daily"
        action.is_medication_action = True
        action.source_authority = "physician"
        verification = MedicationAuthorityVerification(
            id="v-expiry-task2", evidence_id=evidence.id, person_id=person.id,
            source_text_sha256=hashlib.sha256(evidence.source_text.encode("utf-8")).hexdigest(),
            authority_role="physician", verifier_identity="Dr Test",
            verification_method="signed_prescription",
            verified_by_caregiver_id=caregiver.id,
            verified_at=datetime.now(timezone.utc),
            expires_at=datetime(2026, 10, 24, 0, 0, tzinfo=timezone.utc),
        )
        due = datetime(2026, 10, 23, 10, 0, tzinfo=timezone.utc)
        reminder = CareReminder(
            id="r-med-expiry", person_id=person.id, action_id=action.id,
            created_by_caregiver_id=caregiver.id, title="Medication reminder",
            message="Authority may expire before delivery", timezone="UTC",
            recurrence="once", next_run_at=due, local_time="10:00", enabled=True,
        )
        db.add_all([verification, reminder])
        db.commit()
        assert materialize_due_reminders(db, datetime(2026, 10, 23, 11, 0, tzinfo=timezone.utc)) == 1

        verification.expires_at = datetime(2026, 10, 23, 10, 30, tzinfo=timezone.utc)
        db.commit()
        assert deliver_pending_receipts(db, datetime(2026, 10, 23, 11, 1, tzinfo=timezone.utc)) == 0
        receipt = db.query(ReminderDelivery).filter_by(reminder_id=reminder.id).one()
        db.refresh(reminder)
        assert receipt.status == "cancelled"
        assert reminder.enabled is False
        assert db.query(ReminderDeliveryAttempt).filter_by(delivery_id=receipt.id).count() == 0
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_completion_note_is_saved_as_caregiver_report_evidence_atomically():
    engine, db = make_db()
    try:
        person, caregiver, source_evidence, action = seed_action(db)
        result = ActionLifecycleService().record_completion(
            action,
            ActionCompletionEvidence(
                evidence_ids=[], completion_confidence=0.6,
                notes="I completed the clinic follow-up and documented the response.",
            ),
            db,
            completed_by_caregiver_id=caregiver.id,
        )
        assert result.status == "completed"
        assert result.completed_by_caregiver_id == caregiver.id
        report = db.query(Evidence).filter(
            Evidence.person_id == person.id,
            Evidence.type == "caregiver_report",
            Evidence.source_type == "action_completion",
        ).one()
        assert report.id in result.completion_evidence_ids
        assert report.uploaded_by_caregiver_id == caregiver.id
        assert "completed the clinic follow-up" in report.source_text
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_reminder_contract_rejects_blank_text_and_naive_datetime():
    base = {
        "title": "Follow-up",
        "message": "Call the clinic",
        "scheduled_at": datetime(2026, 10, 24, 10, 0, tzinfo=timezone.utc),
        "timezone": "Africa/Lagos",
        "recurrence": "daily",
        "local_time": "11:00",
    }
    assert ReminderCreate(**base).title == "Follow-up"
    try:
        ReminderCreate(**{**base, "title": "   "})
        assert False, "blank reminder title should be rejected"
    except ValidationError:
        pass
    try:
        ReminderCreate(**{**base, "scheduled_at": datetime(2026, 10, 24, 10, 0)})
        assert False, "naive scheduled_at should be rejected"
    except ValidationError:
        pass


def test_changed_medication_source_blocks_action_completion_even_after_prior_verification():
    engine, db = make_db()
    try:
        person, caregiver, evidence, action = seed_action(db)
        action.is_medication_action = True
        action.source_authority = "physician"
        db.add(MedicationAuthorityVerification(
            id="v-stale-completion",
            evidence_id=evidence.id,
            person_id=person.id,
            source_text_sha256=hashlib.sha256(evidence.source_text.encode("utf-8")).hexdigest(),
            authority_role="physician",
            verifier_identity="Trusted prescriber",
            verification_method="signed_prescription",
            verified_by_caregiver_id=caregiver.id,
            verified_at=datetime.now(timezone.utc),
        ))
        db.commit()
        evidence.source_text = "Source content changed after review."
        db.commit()
        try:
            ActionLifecycleService().record_completion(
                action,
                ActionCompletionEvidence(evidence_ids=[], completion_confidence=0.6, notes="Caregiver report."),
                db,
                completed_by_caregiver_id=caregiver.id,
            )
            assert False, "changed medication source must not be completed as authoritative"
        except ValueError as exc:
            assert "source authority" in str(exc).lower()
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_changed_medication_source_suppresses_due_reminder():
    engine, db = make_db()
    try:
        person, caregiver, evidence, action = seed_action(db)
        action.is_medication_action = True
        action.source_authority = "physician"
        db.add(MedicationAuthorityVerification(
            id="v-stale-reminder",
            evidence_id=evidence.id,
            person_id=person.id,
            source_text_sha256=hashlib.sha256(evidence.source_text.encode("utf-8")).hexdigest(),
            authority_role="physician",
            verifier_identity="Trusted prescriber",
            verification_method="signed_prescription",
            verified_by_caregiver_id=caregiver.id,
            verified_at=datetime.now(timezone.utc),
        ))
        reminder = CareReminder(
            id="r-stale-medication", person_id=person.id, action_id=action.id,
            created_by_caregiver_id=caregiver.id, title="Medication reminder",
            message="Follow verified directions", timezone="UTC", recurrence="once",
            next_run_at=datetime(2026, 10, 23, 10, 0, tzinfo=timezone.utc),
            local_time="10:00", enabled=True,
        )
        db.add(reminder)
        db.commit()
        evidence.source_text = "Source content changed after review."
        db.commit()
        assert materialize_due_reminders(db, datetime(2026, 10, 23, 11, 0, tzinfo=timezone.utc)) == 0
        db.refresh(reminder)
        assert reminder.enabled is False
        assert db.query(ReminderDelivery).filter_by(reminder_id=reminder.id).count() == 0
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()
