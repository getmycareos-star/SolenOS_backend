"""Index caregiver membership lookups by care record.

Revision ID: 20261120_06_caregiver_person_index
Revises: 20261120_05_repair_reminder_assignment_backfill
"""
from alembic import op
from sqlalchemy import inspect

revision = "20261120_06_caregiver_person_index"
down_revision = "20261120_05_repair_reminder_assignment_backfill"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("caregivers"):
        raise RuntimeError("caregivers table is missing; cannot add care-record lookup index")
    indexes = {item["name"] for item in inspector.get_indexes("caregivers")}
    if "ix_caregivers_person_id" not in indexes:
        op.create_index(
            "ix_caregivers_person_id",
            "caregivers",
            ["person_id"],
            unique=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if inspector.has_table("caregivers"):
        indexes = {item["name"] for item in inspector.get_indexes("caregivers")}
        if "ix_caregivers_person_id" in indexes:
            op.drop_index("ix_caregivers_person_id", table_name="caregivers")
