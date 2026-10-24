"""Join the legacy caregiver and care-continuity migration histories.

Revision ID: 20261024_07
Revises: 20261022_03, 20261024_06

This is a graph-only merge. The two parent branches perform their own schema
changes. It does not rewrite deployed revision IDs or modify care records.
"""
from alembic import op

revision = "20261024_07"
down_revision = ("20261022_03", "20261024_06")
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    raise RuntimeError(
        "Forward-only migration graph merge; downgrade requires a separately "
        "reviewed migration plan."
    )
