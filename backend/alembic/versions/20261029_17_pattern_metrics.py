"""Persist descriptive inter-event interval metrics for longitudinal patterns.

Revision ID: 20261029_17_pattern_metrics
Revises: 20261029_16_q_intent
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261029_17_pattern_metrics"
down_revision = "20261029_16_q_intent"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("patterns"):
        raise RuntimeError("Patterns table must exist before pattern metrics can be added.")
    columns = {column["name"] for column in inspector.get_columns("patterns")}
    if "pattern_metrics" not in columns:
        # JSONList uses TEXT serialization on both SQLite and PostgreSQL.
        op.add_column("patterns", sa.Column("pattern_metrics", sa.Text(), nullable=True))


def downgrade() -> None:
    raise RuntimeError(
        "Refusing to discard persisted longitudinal pattern metrics during downgrade."
    )
