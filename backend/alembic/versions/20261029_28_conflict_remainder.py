"""Persist the unresolved remainder of a partial conflict resolution.

Revision ID: 20261029_28_conflict_remainder
Revises: 20261029_27_completeness
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261029_28_conflict_remainder"
down_revision = "20261029_27_completeness"
branch_labels = None
depends_on = None


def _add_if_missing(table: str, name: str, column: sa.Column) -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table(table):
        raise RuntimeError(f"Required conflict history table is missing: {table}")
    columns = {item["name"] for item in inspector.get_columns(table)}
    if name not in columns:
        op.add_column(table, column)


def upgrade() -> None:
    _add_if_missing("contradictions", "resolution_scope", sa.Column("resolution_scope", sa.Text(), nullable=True))
    _add_if_missing("contradictions", "unresolved_remainder", sa.Column("unresolved_remainder", sa.Text(), nullable=True))
    _add_if_missing("contradiction_resolution_events", "unresolved_remainder", sa.Column("unresolved_remainder", sa.Text(), nullable=True))


def downgrade() -> None:
    raise RuntimeError("Refusing to discard partial conflict-resolution scope or unresolved-remainder history.")
