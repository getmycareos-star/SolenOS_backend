"""Merge the Qwen candidate and reminder migration branches.

Revision ID: 20261025_02
Revises: 20261024_12, 20261025_01
"""
from alembic import op

revision = "20261025_02"
down_revision = ("20261024_12", "20261025_01")
branch_labels = None
depends_on = None


def upgrade():
    # Both parent revisions carry their own idempotent schema changes.
    pass


def downgrade():
    # Downgrading a merged production history is intentionally not automated.
    raise RuntimeError("Cannot safely downgrade the merged Qwen/reminder migration head.")
