"""Create durable reminder definitions and idempotent delivery receipts.

Revision ID: t2_reminders_20261023
Revises: t2_completion_20261023
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "t2_reminders_20261023"
down_revision = "t2_completion_20261023"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("care_reminders"):
        op.create_table(
            "care_reminders",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("person_id", sa.String(), sa.ForeignKey("persons.id"), nullable=False),
            sa.Column("action_id", sa.String(), sa.ForeignKey("actions.id"), nullable=False),
            sa.Column("created_by_caregiver_id", sa.String(), sa.ForeignKey("caregivers.id"), nullable=False),
            sa.Column("title", sa.String(), nullable=False),
            sa.Column("message", sa.Text(), nullable=False),
            sa.Column("timezone", sa.String(), nullable=False),
            sa.Column("recurrence", sa.String(), nullable=False, server_default="once"),
            sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("local_time", sa.String(), nullable=False),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="5"),
            sa.Column("retry_delay_seconds", sa.Integer(), nullable=False, server_default="60"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        )
    inspector = inspect(bind)
    for name, columns in (
        ("ix_care_reminders_person_id", ["person_id"]),
        ("ix_care_reminders_action_id", ["action_id"]),
        ("ix_care_reminders_next_run_at", ["next_run_at"]),
        ("ix_care_reminders_enabled", ["enabled"]),
    ):
        if inspector.has_table("care_reminders") and name not in {i["name"] for i in inspect(bind).get_indexes("care_reminders")}:
            op.create_index(name, "care_reminders", columns, unique=False)

    inspector = inspect(bind)
    if not inspector.has_table("reminder_deliveries"):
        op.create_table(
            "reminder_deliveries",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("reminder_id", sa.String(), sa.ForeignKey("care_reminders.id"), nullable=False),
            sa.Column("person_id", sa.String(), sa.ForeignKey("persons.id"), nullable=False),
            sa.Column("idempotency_key", sa.String(), nullable=False, unique=True),
            sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
            sa.Column("status", sa.String(), nullable=False, server_default="pending"),
            sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint("reminder_id", "scheduled_for", name="uq_reminder_delivery_occurrence"),
        )
    inspector = inspect(bind)
    indexes = {i["name"] for i in inspector.get_indexes("reminder_deliveries")}
    for name, columns in (
        ("ix_reminder_deliveries_reminder_id", ["reminder_id"]),
        ("ix_reminder_deliveries_person_id", ["person_id"]),
        ("ix_reminder_deliveries_status", ["status"]),
        ("ix_reminder_deliveries_next_attempt_at", ["next_attempt_at"]),
        ("ix_reminder_delivery_due", ["status", "next_attempt_at"]),
    ):
        if name not in indexes:
            op.create_index(name, "reminder_deliveries", columns, unique=False)


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if inspector.has_table("reminder_deliveries"):
        for index in list(inspect(bind).get_indexes("reminder_deliveries")):
            op.drop_index(index["name"], table_name="reminder_deliveries")
        op.drop_table("reminder_deliveries")
    inspector = inspect(bind)
    if inspector.has_table("care_reminders"):
        for index in list(inspect(bind).get_indexes("care_reminders")):
            op.drop_index(index["name"], table_name="care_reminders")
        op.drop_table("care_reminders")
