"""Merge the care-circle and Delta Engine migration branches.

Revision ID: 20261023_05
Revises: 20261023_01, 20261023_04
Create Date: 2026-10-23
"""
from alembic import op

revision = "20261023_05"
down_revision = ("20261023_01", "20261023_04")
branch_labels = None
depends_on = None


def upgrade():
    # This is a graph merge only. Both parent migrations already create their
    # own tables; no schema operation is required here.
    pass


def downgrade():
    # Downgrading this merge node does not reverse either parent migration.
    # Alembic will expose the two parent heads again.
    pass
