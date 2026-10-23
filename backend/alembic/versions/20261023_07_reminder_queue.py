"""Add persistent reminder scheduling and delivery-attempt receipts.

Revision ID: 20261023_07
Revises: 20261023_06
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261023_07"
down_revision = "20261023_06"
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
            sa.Column("action_id", sa.String(), sa.ForeignKey("actions.id"), nullable=True),
            sa.Column("created_by_caregiver_id", sa.String(), sa.ForeignKey("caregivers.id"), nullable=False),
            sa.Column("title", sa.String(), nullable=False),
            sa.Column("message", sa.Text(), nullable=False),
            sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("timezone", sa.String(), nullable=False, server_default="UTC"),
            sa.Column("status", sa.String(), nullable=False, server_default="scheduled"),
            sa.Column("idempotency_key", sa.String(), nullable=False),
            sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="5"),
            sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint("person_id", "idempotency_key", name="uq_care_reminders_person_idempotency"),
        )
    inspector = inspect(bind)
    if inspector.has_table("care_reminders"):
        indexes = {item["name"] for item in inspector.get_indexes("care_reminders")}
        for name, columns in (
            ("ix_care_reminders_person_id", ["person_id"]),
            ("ix_care_reminders_action_id", ["action_id"]),
            ("ix_care_reminders_created_by_caregiver_id", ["created_by_caregiver_id"]),
            ("ix_care_reminders_due_status", ["status", "next_attempt_at"]),
            ("ix_care_reminders_person_due", ["person_id", "due_at"]),
        ):
            if name not in indexes:
                op.create_index(name, "care_reminders", columns)

    inspector = inspect(bind)
    if not inspector.has_table("reminder_delivery_attempts"):
        op.create_table(
            "reminder_delivery_attempts",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("reminder_id", sa.String(), sa.ForeignKey("care_reminders.id"), nullable=False),
            sa.Column("attempt_number", sa.Integer(), nullable=False),
            sa.Column("outcome", sa.String(), nullable=False),
            sa.Column("provider", sa.String(), nullable=True),
            sa.Column("provider_receipt_id", sa.String(), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
    indexes = {item["name"] for item in inspect(bind).get_indexes("reminder_delivery_attempts")}
    for name, columns in (
        ("ix_reminder_delivery_attempts_reminder_id", ["reminder_id"]),
        ("ix_reminder_delivery_attempts_reminder_created", ["reminder_id", "attempted_at"]),
    ):
        if name not in indexes:
            op.create_index(name, "reminder_delivery_attempts", columns)


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if inspector.has_table("reminder_delivery_attempts"):
        op.drop_table("reminder_delivery_attempts")
    if inspector.has_table("care_reminders"):
        op.drop_table("care_reminders")
