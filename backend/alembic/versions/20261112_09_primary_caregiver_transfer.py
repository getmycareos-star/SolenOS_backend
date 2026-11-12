"""Persist separately accepted primary caregiver authority transfers.

Revision ID: 20261112_09_primary_caregiver_transfer
Revises: 20261112_08_open_loop_assignment
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261112_09_primary_caregiver_transfer"
down_revision = "20261112_08_open_loop_assignment"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if not inspect(bind).has_table("caregiver_primary_transfers"):
        op.create_table(
            "caregiver_primary_transfers",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("person_id", sa.String(), sa.ForeignKey("persons.id", ondelete="CASCADE"), nullable=False),
            sa.Column("from_caregiver_id", sa.String(), sa.ForeignKey("caregivers.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("to_caregiver_id", sa.String(), sa.ForeignKey("caregivers.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("reason", sa.String(1000), nullable=True),
            sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("accepted_by_caregiver_id", sa.String(), sa.ForeignKey("caregivers.id", ondelete="SET NULL"), nullable=True),
        )
        op.create_index("ix_caregiver_primary_transfers_person_id", "caregiver_primary_transfers", ["person_id"])
        op.create_index("ix_caregiver_primary_transfers_status", "caregiver_primary_transfers", ["status"])


def downgrade():
    bind = op.get_bind()
    if inspect(bind).has_table("caregiver_primary_transfers"):
        for item in inspect(bind).get_indexes("caregiver_primary_transfers"):
            op.drop_index(item["name"], table_name="caregiver_primary_transfers")
        op.drop_table("caregiver_primary_transfers")
