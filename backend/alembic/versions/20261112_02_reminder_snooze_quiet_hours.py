"""Add reminder snooze state and real caregiver quiet-hours schedule.

Revision ID: 20261112_02_reminder_snooze_quiet_hours
Revises: 20261112_01_care_execution_status
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261112_02_reminder_snooze_quiet_hours"
down_revision = "20261112_01_care_execution_status"
branch_labels = None
depends_on = None


def _add_column_if_missing(table, column):
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table(table):
        return
    names = {item["name"] for item in inspector.get_columns(table)}
    if column.name not in names:
        op.add_column(table, column)


def upgrade():
    _add_column_if_missing("care_reminders", sa.Column("snoozed_at", sa.DateTime(timezone=True), nullable=True))
    _add_column_if_missing("care_reminders", sa.Column("snoozed_by_caregiver_id", sa.String(), sa.ForeignKey("caregivers.id"), nullable=True))
    _add_column_if_missing("care_reminders", sa.Column("snooze_count", sa.Integer(), nullable=False, server_default="0"))
    _add_column_if_missing("care_reminders", sa.Column("delivery_generation", sa.Integer(), nullable=False, server_default="0"))
    _add_column_if_missing("care_notification_preferences", sa.Column("quiet_hours_start", sa.String(5), nullable=False, server_default="21:00"))
    _add_column_if_missing("care_notification_preferences", sa.Column("quiet_hours_end", sa.String(5), nullable=False, server_default="08:00"))
    _add_column_if_missing("care_notification_preferences", sa.Column("timezone_name", sa.String(64), nullable=False, server_default="UTC"))


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    for table, columns in (
        ("care_notification_preferences", ("timezone_name", "quiet_hours_end", "quiet_hours_start")),
        ("care_reminders", ("delivery_generation", "snooze_count", "snoozed_by_caregiver_id", "snoozed_at")),
    ):
        if not inspector.has_table(table):
            continue
        existing = {item["name"] for item in inspector.get_columns(table)}
        for name in columns:
            if name in existing:
                op.drop_column(table, name)
