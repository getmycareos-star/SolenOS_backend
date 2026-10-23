from datetime import datetime, timezone, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models.care import Person, Caregiver
from app.models.reminder import CareReminder, ReminderDeliveryAttempt
from app.services.reminder_delivery import process_due_reminders


def make_db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    session.add(Person(id="p1", name="Person"))
    session.add(Caregiver(id="c1", name="Caregiver", email="c1@example.test", relationship="family", person_id="p1"))
    session.commit()
    return engine, session


def test_reminder_delivery_persists_provider_receipt():
    engine, db = make_db()
    try:
        now = datetime.now(timezone.utc)
        reminder = CareReminder(
            id="r1", person_id="p1", created_by_caregiver_id="c1",
            title="Appointment", message="Appointment is soon", due_at=now - timedelta(minutes=1),
            timezone="UTC", status="scheduled", idempotency_key="stable-key-0001",
            attempt_count=0, max_attempts=3, next_attempt_at=now - timedelta(minutes=1),
        )
        db.add(reminder)
        db.commit()
        result = process_due_reminders(
            db, lambda item, key: {"provider": "test-provider", "receipt_id": "receipt-1"}, now=now,
        )
        db.refresh(reminder)
        attempt = db.query(ReminderDeliveryAttempt).filter_by(reminder_id="r1").one()
        assert result["delivered"] == 1
        assert reminder.status == "delivered"
        assert reminder.delivered_at is not None
        assert attempt.provider_receipt_id == "receipt-1"
        assert attempt.outcome == "delivered"
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_reminder_delivery_failure_schedules_exponential_retry():
    engine, db = make_db()
    try:
        now = datetime.now(timezone.utc)
        reminder = CareReminder(
            id="r2", person_id="p1", created_by_caregiver_id="c1",
            title="Appointment", message="Appointment is soon", due_at=now - timedelta(minutes=1),
            timezone="UTC", status="scheduled", idempotency_key="stable-key-0002",
            attempt_count=0, max_attempts=3, next_attempt_at=now - timedelta(minutes=1),
        )
        db.add(reminder)
        db.commit()
        result = process_due_reminders(db, lambda item, key: (_ for _ in ()).throw(RuntimeError("provider unavailable")), now=now)
        db.refresh(reminder)
        attempt = db.query(ReminderDeliveryAttempt).filter_by(reminder_id="r2").one()
        assert result["retry_scheduled"] == 1
        assert reminder.status == "scheduled"
        assert reminder.attempt_count == 1
        assert reminder.next_attempt_at is not None
        assert attempt.outcome == "failed"
        assert "provider unavailable" in attempt.error_message
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_recurring_reminder_creates_next_occurrence_in_local_timezone():
    engine, db = make_db()
    try:
        now = datetime.now(timezone.utc)
        reminder = CareReminder(
            id="r-recurring", person_id="p1", created_by_caregiver_id="c1",
            title="Daily check-in", message="Record today's update",
            due_at=now - timedelta(minutes=1), timezone="Africa/Lagos",
            recurrence_rule="daily", status="scheduled",
            idempotency_key="series-daily-0001", attempt_count=0,
            max_attempts=3, next_attempt_at=now - timedelta(minutes=1),
        )
        db.add(reminder)
        db.commit()
        result = process_due_reminders(
            db, lambda item, key: {"provider": "test-provider", "receipt_id": "receipt-recurring"}, now=now,
        )
        next_item = db.query(CareReminder).filter(
            CareReminder.person_id == "p1",
            CareReminder.idempotency_key.like("series-daily-0001:%"),
        ).one()
        assert result["delivered"] == 1
        assert reminder.status == "delivered"
        assert next_item.status == "scheduled"
        assert next_item.recurrence_rule == "daily"
        assert next_item.due_at > reminder.due_at
        assert next_item.timezone == "Africa/Lagos"
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()
