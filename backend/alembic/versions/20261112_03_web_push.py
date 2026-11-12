"""Persist encrypted Web Push subscriptions and delivery receipts.

Revision ID: 20261112_04_web_push
Revises: 20261112_02_reminder_snooze_quiet_hours
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261112_04_web_push"
down_revision = "20261112_03_snooze_quiet"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("care_push_subscriptions"):
        op.create_table(
            "care_push_subscriptions",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("person_id", sa.String(), sa.ForeignKey("persons.id", ondelete="CASCADE"), nullable=False),
            sa.Column("caregiver_id", sa.String(), sa.ForeignKey("caregivers.id", ondelete="CASCADE"), nullable=False),
            sa.Column("endpoint_hash", sa.String(64), nullable=False),
            sa.Column("encrypted_subscription", sa.Text(), nullable=False),
            sa.Column("user_agent", sa.String(255), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint("endpoint_hash", name="uq_care_push_subscriptions_endpoint_hash"),
        )
    inspector = inspect(bind)
    if not inspector.has_table("care_push_deliveries"):
        op.create_table(
            "care_push_deliveries",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("notification_id", sa.String(), sa.ForeignKey("care_notifications.id", ondelete="CASCADE"), nullable=False),
            sa.Column("subscription_id", sa.String(), sa.ForeignKey("care_push_subscriptions.id", ondelete="CASCADE"), nullable=False),
            sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
            sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("provider_accepted_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_error_code", sa.String(64), nullable=True),
            sa.Column("last_error_message", sa.Text(), nullable=True),
            sa.UniqueConstraint("notification_id", "subscription_id", name="uq_care_push_delivery_target"),
        )
    inspector = inspect(bind)
    for table, indexes in (
        ("care_push_subscriptions", (("ix_care_push_subscriptions_person_id", ["person_id"]), ("ix_care_push_subscriptions_caregiver_id", ["caregiver_id"]), ("ix_care_push_subscriptions_endpoint_hash", ["endpoint_hash"]))),
        ("care_push_deliveries", (("ix_care_push_deliveries_notification_id", ["notification_id"]), ("ix_care_push_deliveries_subscription_id", ["subscription_id"]), ("ix_care_push_deliveries_status", ["status"]), ("ix_care_push_deliveries_next_attempt_at", ["next_attempt_at"]))),
    ):
        if inspector.has_table(table):
            existing = {item["name"] for item in inspect(bind).get_indexes(table)}
            for name, columns in indexes:
                if name not in existing:
                    op.create_index(name, table, columns)


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if inspector.has_table("care_push_deliveries"):
        op.drop_table("care_push_deliveries")
    if inspect(bind).has_table("care_push_subscriptions"):
        op.drop_table("care_push_subscriptions")
