"""Add minimal operational event storage.

Revision ID: 20261023_06
Revises: 20261023_05, t2_delivery_attempts_20261023
"""
from alembic import op
import sqlalchemy as sa

revision = "20261023_06"
down_revision = ("20261023_05", "t2_delivery_attempts_20261023")
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "solen_events",
        sa.Column("id", sa.String(), primary_key=True, nullable=False),
        sa.Column("user_id_hash", sa.String(length=64), nullable=True),
        sa.Column("event_name", sa.String(length=80), nullable=False),
        sa.Column("session_id_hash", sa.String(length=64), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_solen_events_user_id_hash", "solen_events", ["user_id_hash"])
    op.create_index("ix_solen_events_event_name", "solen_events", ["event_name"])
    op.create_index("ix_solen_events_session_id_hash", "solen_events", ["session_id_hash"])
    op.create_index("ix_solen_events_created_at", "solen_events", ["created_at"])


def downgrade():
    op.drop_index("ix_solen_events_created_at", table_name="solen_events")
    op.drop_index("ix_solen_events_session_id_hash", table_name="solen_events")
    op.drop_index("ix_solen_events_event_name", table_name="solen_events")
    op.drop_index("ix_solen_events_user_id_hash", table_name="solen_events")
    op.drop_table("solen_events")
