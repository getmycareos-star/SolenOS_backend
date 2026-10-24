"""Add care-record-scoped notifications and preferences.

Revision ID: 20261024_10
Revises: 20261024_09
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261024_10"
down_revision = "20261024_09"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("care_notifications"):
        op.create_table(
            "care_notifications",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("person_id", sa.String(), sa.ForeignKey("persons.id", ondelete="CASCADE"), nullable=False),
            sa.Column("caregiver_id", sa.String(), sa.ForeignKey("caregivers.id", ondelete="CASCADE"), nullable=False),
            sa.Column("title", sa.String(180), nullable=False),
            sa.Column("body", sa.Text(), nullable=False),
            sa.Column("category", sa.String(40), nullable=False, server_default="care_update"),
            sa.Column("priority", sa.String(20), nullable=False, server_default="normal"),
            sa.Column("source_event_id", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        )
    inspector = inspect(bind)
    if inspector.has_table("care_notifications"):
        indexes = {i["name"] for i in inspector.get_indexes("care_notifications")}
        for name, columns in [
            ("ix_care_notifications_person_id", ["person_id"]),
            ("ix_care_notifications_caregiver_id", ["caregiver_id"]),
            ("ix_care_notifications_created_at", ["created_at"]),
            ("ix_care_notifications_read_at", ["read_at"]),
        ]:
            if name not in indexes:
                op.create_index(name, "care_notifications", columns)
    if not inspect(bind).has_table("care_notification_preferences"):
        op.create_table(
            "care_notification_preferences",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("person_id", sa.String(), sa.ForeignKey("persons.id", ondelete="CASCADE"), nullable=False),
            sa.Column("caregiver_id", sa.String(), sa.ForeignKey("caregivers.id", ondelete="CASCADE"), nullable=False),
            sa.Column("quiet_hours_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("reminders_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint("person_id", "caregiver_id", name="uq_care_notification_preferences_actor"),
        )
    inspector = inspect(bind)
    if inspector.has_table("care_notification_preferences"):
        indexes = {i["name"] for i in inspector.get_indexes("care_notification_preferences")}
        for name, columns in [
            ("ix_care_notification_preferences_person_id", ["person_id"]),
            ("ix_care_notification_preferences_caregiver_id", ["caregiver_id"]),
        ]:
            if name not in indexes:
                op.create_index(name, "care_notification_preferences", columns)


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if inspector.has_table("care_notification_preferences"):
        op.drop_table("care_notification_preferences")
    if inspect(bind).has_table("care_notifications"):
        op.drop_table("care_notifications")
