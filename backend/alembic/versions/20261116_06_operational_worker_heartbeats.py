"""Persist operational worker heartbeats for privacy export expiry.

Revision ID: 20261116_06_operational_worker_heartbeats
Revises: 20261116_05_incident_events_append_only
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261116_06_operational_worker_heartbeats"
down_revision = "20261116_05_incident_events_append_only"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    table_name = "operational_worker_heartbeats"

    # CREATE TABLE IF NOT EXISTS closes the check-then-create race that can
    # occur when an ORM bootstrap and Alembic migration reach the same database
    # concurrently. It is safe for a clean database and for a complete table
    # already materialized by the registered ORM metadata.
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS operational_worker_heartbeats (
            worker_name VARCHAR(100) NOT NULL PRIMARY KEY,
            last_started_at TIMESTAMP WITH TIME ZONE NOT NULL,
            last_completed_at TIMESTAMP WITH TIME ZONE NOT NULL,
            last_success_at TIMESTAMP WITH TIME ZONE,
            last_outcome VARCHAR(20) NOT NULL,
            last_error_type VARCHAR(120),
            last_processed_count INTEGER DEFAULT 0 NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL
        )
    """))

    inspector = inspect(bind)
    actual = {column["name"] for column in inspector.get_columns(table_name)}
    required = {
        "worker_name", "last_started_at", "last_completed_at",
        "last_success_at", "last_outcome", "last_error_type",
        "last_processed_count", "updated_at",
    }
    missing = sorted(required - actual)
    if missing:
        raise RuntimeError(
            "Existing operational worker heartbeat table is incomplete: "
            + ", ".join(missing)
        )

    primary_key = tuple(inspector.get_pk_constraint(table_name).get("constrained_columns") or ())
    if primary_key != ("worker_name",):
        raise RuntimeError(
            "Existing operational worker heartbeat table has an invalid primary key: "
            + repr(primary_key)
        )

    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_operational_worker_heartbeats_last_success_at "
        "ON operational_worker_heartbeats (last_success_at)"
    ))


def downgrade():
    bind = op.get_bind()
    table_name = "operational_worker_heartbeats"
    inspector = inspect(bind)
    if inspector.has_table(table_name):
        indexes = {item["name"] for item in inspector.get_indexes(table_name)}
        index_name = "ix_operational_worker_heartbeats_last_success_at"
        if index_name in indexes:
            op.drop_index(index_name, table_name=table_name)
        op.drop_table(table_name)
