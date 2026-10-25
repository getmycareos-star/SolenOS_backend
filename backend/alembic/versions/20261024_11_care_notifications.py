"""Reconcile caregiver consent and care notification tables across deployed histories.

Revision ID: 20261024_11
Revises: 20261024_10

This compatibility revision is idempotent. The deployed database already records
20261024_11 from the earlier consent/notification lineage; fresh installs now
reach this revision from the current confidence-cleanup revision. Both paths
must expose the same consent and notification tables.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261024_11"
down_revision = "20261024_10"
branch_labels = None
depends_on = None


def _ensure_indexes(bind, table, definitions):
    inspector = inspect(bind)
    if not inspector.has_table(table):
        return
    existing = {item["name"] for item in inspector.get_indexes(table)}
    for name, columns in definitions:
        if name not in existing:
            op.create_index(name, table, columns)


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)

    if not inspector.has_table("caregiver_consents"):
        op.create_table(
            "caregiver_consents",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("person_id", sa.String(), sa.ForeignKey("persons.id"), nullable=False),
            sa.Column("caregiver_id", sa.String(), sa.ForeignKey("caregivers.id"), nullable=False),
            sa.Column("medical_disclaimer_acknowledged", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("privacy_model_acknowledged", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("multi_caregiver_acknowledged", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("no_advertising_acknowledged", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("data_improvement_consent", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("accepted_terms_version", sa.String(), nullable=False, server_default="2026-10"),
            sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint("person_id", "caregiver_id", name="uq_caregiver_consent_person_actor"),
        )
    _ensure_indexes(bind, "caregiver_consents", [
        ("ix_caregiver_consents_person_id", ["person_id"]),
        ("ix_caregiver_consents_caregiver_id", ["caregiver_id"]),
    ])

    if not inspect(bind).has_table("care_notifications"):
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
    _ensure_indexes(bind, "care_notifications", [
        ("ix_care_notifications_person_id", ["person_id"]),
        ("ix_care_notifications_caregiver_id", ["caregiver_id"]),
        ("ix_care_notifications_created_at", ["created_at"]),
        ("ix_care_notifications_read_at", ["read_at"]),
    ])

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
    _ensure_indexes(bind, "care_notification_preferences", [
        ("ix_care_notification_preferences_person_id", ["person_id"]),
        ("ix_care_notification_preferences_caregiver_id", ["caregiver_id"]),
    ])


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    for table in ("care_notification_preferences", "care_notifications", "caregiver_consents"):
        if inspector.has_table(table):
            op.drop_table(table)
