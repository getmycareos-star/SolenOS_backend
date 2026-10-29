"""Persist the scope of partial conflict resolutions.

Revision ID: 20261029_13_conflict_scope
Revises: 20261029_12_claim_deps
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261029_13_conflict_scope"
down_revision = "20261029_12_claim_deps"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("contradiction_resolution_events"):
        raise RuntimeError("Conflict resolution history must exist before adding resolution scope.")
    columns = {column["name"] for column in inspector.get_columns("contradiction_resolution_events")}
    if "resolution_scope" not in columns:
        op.add_column(
            "contradiction_resolution_events",
            sa.Column("resolution_scope", sa.Text(), nullable=True),
        )


def downgrade() -> None:
    raise RuntimeError(
        "Refusing to discard resolution scope from immutable conflict-resolution history."
    )
