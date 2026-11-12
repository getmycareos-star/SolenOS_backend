"""Track next quiet-hours release time for due reminder notifications.

Revision ID: 20261112_04_quiet_hours_release
Revises: 20261112_03_web_push
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261112_04_quiet_hours_release"
down_revision = "20261112_03_web_push"
branch_labels = None
depends_on = None


def upgrade():
    inspector = inspect(op.get_bind())
    if inspector.has_table("care_reminders"):
        columns = {item["name"] for item in inspector.get_columns("care_reminders")}
        if "notification_deferred_until" not in columns:
            op.add_column("care_reminders", sa.Column("notification_deferred_until", sa.DateTime(timezone=True), nullable=True))
        indexes = {item["name"] for item in inspect(op.get_bind()).get_indexes("care_reminders")}
        if "ix_care_reminders_notification_deferred_until" not in indexes:
            op.create_index("ix_care_reminders_notification_deferred_until", "care_reminders", ["notification_deferred_until"])


def downgrade():
    inspector = inspect(op.get_bind())
    if inspector.has_table("care_reminders"):
        indexes = {item["name"] for item in inspector.get_indexes("care_reminders")}
        if "ix_care_reminders_notification_deferred_until" in indexes:
            op.drop_index("ix_care_reminders_notification_deferred_until", table_name="care_reminders")
        columns = {item["name"] for item in inspect(op.get_bind()).get_columns("care_reminders")}
        if "notification_deferred_until" in columns:
            op.drop_column("care_reminders", "notification_deferred_until")
