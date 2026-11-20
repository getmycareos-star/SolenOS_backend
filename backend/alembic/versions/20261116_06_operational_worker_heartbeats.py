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
    inspector = inspect(bind)
    table_name = "operational_worker_heartbeats"
    if not inspector.has_table(table_name):
        op.create_table(
            table_name,
            sa.Column("worker_name", sa.String(100), primary_key=True),
            sa.Column("last_started_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("last_completed_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_outcome", sa.String(20), nullable=False),
            sa.Column("last_error_type", sa.String(120), nullable=True),
            sa.Column("last_processed_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
        inspector = inspect(bind)
    else:
        # Test/bootstrap environments can materialize all registered ORM
        # tables before Alembic starts. Treat that as an existing schema, not
        # as permission to fail on CREATE TABLE or silently accept drift.
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

    indexes = {item["name"] for item in inspect(bind).get_indexes(table_name)}
    index_name = "ix_operational_worker_heartbeats_last_success_at"
    if index_name not in indexes:
        op.create_index(index_name, table_name, ["last_success_at"])


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    table_name = "operational_worker_heartbeats"
    if inspector.has_table(table_name):
        indexes = {item["name"] for item in inspector.get_indexes(table_name)}
        index_name = "ix_operational_worker_heartbeats_last_success_at"
        if index_name in indexes:
            op.drop_index(index_name, table_name=table_name)
        op.drop_table(table_name)
