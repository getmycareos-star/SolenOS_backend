"""Add lifecycle state and deduplication for proactive care notifications."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261112_06_notification_lifecycle"
down_revision = "20261112_05_quiet_release"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("care_notifications")}

    # The application metadata may have created part of the schema in test
    # environments. Add only fields that are not already present.
    additions = (
        ("status", sa.Column("status", sa.String(length=16), nullable=False, server_default="active")),
        ("updated_at", sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())),
        ("resolved_at", sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True)),
        ("resolution_reason", sa.Column("resolution_reason", sa.String(length=80), nullable=True)),
    )
    for name, column in additions:
        if name not in columns:
            op.add_column("care_notifications", column)
            columns.add(name)

    # Preserve historical notifications but supersede duplicate active messages
    # before enforcing one active message per caregiver/source/category.
    op.execute(sa.text("""
        WITH ranked AS (
            SELECT
                id,
                ROW_NUMBER() OVER (
                    PARTITION BY person_id, caregiver_id, category, source_event_id
                    ORDER BY created_at ASC, id ASC
                ) AS position
            FROM care_notifications
            WHERE category IN ('care_change', 'care_attention')
              AND source_event_id IS NOT NULL
        )
        UPDATE care_notifications AS notification
        SET status = 'superseded',
            resolved_at = COALESCE(notification.created_at, CURRENT_TIMESTAMP),
            resolution_reason = 'legacy_duplicate'
        FROM ranked
        WHERE notification.id = ranked.id
          AND ranked.position > 1
          AND notification.status = 'active'
    """))
    op.execute(sa.text("""
        UPDATE care_push_deliveries
        SET status = 'cancelled',
            last_error_code = 'source_duplicate_superseded',
            last_error_message = 'Duplicate historical notification was superseded'
        WHERE status = 'pending'
          AND notification_id IN (
              SELECT id
              FROM care_notifications
              WHERE status = 'superseded'
                AND resolution_reason = 'legacy_duplicate'
          )
    """))

    existing_indexes = {item["name"] for item in inspect(bind).get_indexes("care_notifications")}
    if "uq_care_notifications_active_source" not in existing_indexes:
        op.create_index(
            "uq_care_notifications_active_source",
            "care_notifications",
            ["person_id", "caregiver_id", "category", "source_event_id"],
            unique=True,
            postgresql_where=sa.text(
                "status = 'active' AND category IN ('care_change', 'care_attention') AND source_event_id IS NOT NULL"
            ),
        )
    if "ix_care_notifications_scope_status_created" not in existing_indexes:
        op.create_index(
            "ix_care_notifications_scope_status_created",
            "care_notifications",
            ["person_id", "caregiver_id", "status", "created_at"],
            unique=False,
        )


def downgrade():
    bind = op.get_bind()
    existing_indexes = {item["name"] for item in inspect(bind).get_indexes("care_notifications")}
    if "ix_care_notifications_scope_status_created" in existing_indexes:
        op.drop_index("ix_care_notifications_scope_status_created", table_name="care_notifications")
    if "uq_care_notifications_active_source" in existing_indexes:
        op.drop_index("uq_care_notifications_active_source", table_name="care_notifications")
    columns = {column["name"] for column in inspect(bind).get_columns("care_notifications")}
    for name in ("resolution_reason", "resolved_at", "updated_at", "status"):
        if name in columns:
            op.drop_column("care_notifications", name)
