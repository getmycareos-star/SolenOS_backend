"""Persist operational worker heartbeats for privacy export expiry.

Revision ID: 20261116_06_operational_worker_heartbeats
Revises: 20261116_05_incident_events_append_only
"""
from alembic import op
import sqlalchemy as sa

revision = "20261116_06_operational_worker_heartbeats"
down_revision = "20261116_05_incident_events_append_only"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "operational_worker_heartbeats",
        sa.Column("worker_name", sa.String(100), primary_key=True),
        sa.Column("last_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_outcome", sa.String(20), nullable=False),
        sa.Column("last_error_type", sa.String(120), nullable=True),
        sa.Column("last_processed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_operational_worker_heartbeats_last_success_at",
        "operational_worker_heartbeats",
        ["last_success_at"],
    )


def downgrade():
    op.drop_index(
        "ix_operational_worker_heartbeats_last_success_at",
        table_name="operational_worker_heartbeats",
    )
    op.drop_table("operational_worker_heartbeats")
