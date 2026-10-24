"""Persist proactive care coordination cues on daily intelligence snapshots.

Revision ID: t5_proactive_items_20261024
Revises: t2_delivery_attempts_20261023
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "t5_proactive_items_20261024"
down_revision = "t5_handoff_20261023"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("daily_intelligence"):
        raise RuntimeError("Cannot add proactive intelligence: daily_intelligence table is missing")
    columns = {column["name"] for column in inspector.get_columns("daily_intelligence")}
    if "proactive_items" not in columns:
        op.add_column(
            "daily_intelligence",
            sa.Column("proactive_items", sa.Text(), nullable=True),
        )


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if inspector.has_table("daily_intelligence"):
        columns = {column["name"] for column in inspector.get_columns("daily_intelligence")}
        if "proactive_items" in columns:
            op.drop_column("daily_intelligence", "proactive_items")
