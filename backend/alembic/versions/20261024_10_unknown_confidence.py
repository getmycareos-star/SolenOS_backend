"""Preserve uncalibrated confidence as unknown instead of numeric defaults.

Revision ID: 20261024_10
Revises: 20261024_09
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261024_10"
down_revision = "20261024_09"
branch_labels = None
depends_on = None

# These confidence fields have been populated by unvalidated heuristics/defaults.
# Keep source records, but clear the unsupported score so consumers cannot mistake
# it for a calibrated probability.
_CONFIDENCE_TABLES = (
    "temporal_facts",
    "temporal_relations",
    "reasoning_memory",
    "changes",
    "patterns",
    "situations",
)


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    existing_tables = set(inspector.get_table_names())
    for table in _CONFIDENCE_TABLES:
        if table not in existing_tables:
            continue
        columns = {column["name"] for column in inspector.get_columns(table)}
        if "confidence" not in columns:
            continue
        op.alter_column(
            table,
            "confidence",
            existing_type=sa.Float(),
            nullable=True,
            server_default=None,
        )
        op.execute(sa.text(f"UPDATE {table} SET confidence = NULL"))


def downgrade():
    raise RuntimeError(
        "Cannot safely restore uncalibrated confidence scores. "
        "Restore from a verified backup if a rollback is required."
    )
