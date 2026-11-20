"""Repair care-notification attempt indexes after legacy table rename.

Revision ID: 20261120_09_notification_attempt_indexes
Revises: 20261120_08_allow_redacted_subject_id
"""
from alembic import op
from sqlalchemy import inspect

revision = "20261120_09_notification_attempt_indexes"
down_revision = "20261120_08_allow_redacted_subject_id"
branch_labels = None
depends_on = None

TABLE = "notification_delivery_attempts"
INDEXED_COLUMNS = (
    "actor_id",
    "subject_id",
    "person_id",
    "caregiver_id",
    "notification_id",
    "attempted_at",
    "request_id",
    "correlation_id",
)


def _has_non_unique_single_column_index(bind, table_name, column_name):
    inspector = inspect(bind)
    for index in inspector.get_indexes(table_name):
        columns = tuple(index.get("column_names") or ())
        if columns == (column_name,) and not bool(index.get("unique", False)):
            return True
    return False


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table(TABLE):
        raise RuntimeError(
            "Cannot reconcile notification delivery indexes: notification_delivery_attempts is missing"
        )

    columns = {column["name"] for column in inspector.get_columns(TABLE)}
    missing_columns = sorted(set(INDEXED_COLUMNS) - columns)
    if missing_columns:
        raise RuntimeError(
            "Cannot reconcile notification delivery indexes; required columns are missing: "
            + ", ".join(missing_columns)
        )

    for column_name in INDEXED_COLUMNS:
        if _has_non_unique_single_column_index(bind, TABLE, column_name):
            continue
        # The legacy account-security table retained its old index names after
        # being renamed. PostgreSQL index names are schema-global, so the
        # earlier CREATE INDEX IF NOT EXISTS statements could silently skip
        # creating indexes on the new care-delivery table. Use a care-specific
        # name and verify the index by its actual column definition.
        index_name = f"ix_care_notification_delivery_attempts_{column_name}"
        existing_names = {
            index.get("name")
            for table_name in inspect(bind).get_table_names()
            for index in inspect(bind).get_indexes(table_name)
            if index.get("name")
        }
        if index_name in existing_names:
            raise RuntimeError(
                f"Cannot safely create {TABLE}.{column_name} index: {index_name} already exists elsewhere"
            )
        op.create_index(index_name, TABLE, [column_name], unique=False)

    inspector = inspect(bind)
    missing_indexes = [
        column_name
        for column_name in INDEXED_COLUMNS
        if not _has_non_unique_single_column_index(bind, TABLE, column_name)
    ]
    if missing_indexes:
        raise RuntimeError(
            "Care notification delivery-attempt indexes remain incomplete: "
            + ", ".join(missing_indexes)
        )


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table(TABLE):
        return
    existing = {index.get("name") for index in inspector.get_indexes(TABLE)}
    for column_name in INDEXED_COLUMNS:
        index_name = f"ix_care_notification_delivery_attempts_{column_name}"
        if index_name in existing:
            op.drop_index(index_name, table_name=TABLE)
