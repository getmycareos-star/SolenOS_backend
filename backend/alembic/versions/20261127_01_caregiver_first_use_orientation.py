"""Persist first-use orientation completion on the existing caregiver identity.

Revision ID: 20261127_01_caregiver_first_use_orientation
Revises: 20261126_03_companion_proposal_idempotency
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261127_01_caregiver_first_use_orientation"
down_revision = "20261126_03_companion_proposal_idempotency"
branch_labels = None
depends_on = None

COLUMN = "first_use_orientation_completed_at"


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        raise RuntimeError("Caregiver first-use state requires PostgreSQL.")
    inspector = inspect(bind)
    if not inspector.has_table("caregivers"):
        raise RuntimeError("Cannot persist first-use state: caregivers table is missing.")
    columns = {column["name"] for column in inspector.get_columns("caregivers")}
    if COLUMN not in columns:
        op.add_column("caregivers", sa.Column(COLUMN, sa.DateTime(timezone=True), nullable=True))
    final = {column["name"] for column in inspect(bind).get_columns("caregivers")}
    if COLUMN not in final:
        raise RuntimeError("Caregiver first-use state migration did not add its column.")


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    inspector = inspect(bind)
    if inspector.has_table("caregivers"):
        columns = {column["name"] for column in inspector.get_columns("caregivers")}
        if COLUMN in columns:
            op.drop_column("caregivers", COLUMN)
