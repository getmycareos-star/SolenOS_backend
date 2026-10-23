"""Add minimal operational event storage.

Revision ID: 20261023_06
Revises: 20261023_05, t2_delivery_attempts_20261023
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261023_06"
down_revision = ("20261023_05", "t2_delivery_attempts_20261023")
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("solen_events"):
        op.create_table(
            "solen_events",
            sa.Column("id", sa.String(), primary_key=True, nullable=False),
            sa.Column("user_id_hash", sa.String(length=64), nullable=True),
            sa.Column("event_name", sa.String(length=80), nullable=False),
            sa.Column("session_id_hash", sa.String(length=64), nullable=False),
            sa.Column("metadata", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )

    inspector = inspect(bind)
    existing_indexes = {item["name"] for item in inspector.get_indexes("solen_events")}
    indexes = {
        "ix_solen_events_user_id_hash": ["user_id_hash"],
        "ix_solen_events_event_name": ["event_name"],
        "ix_solen_events_session_id_hash": ["session_id_hash"],
        "ix_solen_events_created_at": ["created_at"],
    }
    for index_name, columns in indexes.items():
        if index_name not in existing_indexes:
            op.create_index(index_name, "solen_events", columns)


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("solen_events"):
        return
    existing_indexes = {item["name"] for item in inspector.get_indexes("solen_events")}
    for index_name in (
        "ix_solen_events_created_at",
        "ix_solen_events_session_id_hash",
        "ix_solen_events_event_name",
        "ix_solen_events_user_id_hash",
    ):
        if index_name in existing_indexes:
            op.drop_index(index_name, table_name="solen_events")
    op.drop_table("solen_events")
