"""Repair a missing caregiver responsibility transfer table.

Revision ID: 20261115_02_repair_caregiver_transfers
Revises: 20261115_01_situation_states
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261115_02_repair_caregiver_transfers"
down_revision = "20261115_01b_situation_states"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)

    # Production readiness detected an already-versioned database missing
    # this ORM table. Recreate only the missing table; never rewrite existing
    # transfer history or infer responsibility assignments.
    if not inspector.has_table("caregiver_responsibility_transfers"):
        op.create_table(
            "caregiver_responsibility_transfers",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("person_id", sa.String(), sa.ForeignKey("persons.id", ondelete="CASCADE"), nullable=False),
            sa.Column("source_responsibility_id", sa.String(), sa.ForeignKey("caregiver_responsibilities.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("from_caregiver_profile_id", sa.String(), sa.ForeignKey("caregiver_profiles.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("to_caregiver_profile_id", sa.String(), sa.ForeignKey("caregiver_profiles.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("created_by_caregiver_id", sa.String(), sa.ForeignKey("caregivers.id", ondelete="SET NULL"), nullable=True),
            sa.Column("related_record_type", sa.String(40), nullable=True),
            sa.Column("related_record_id", sa.String(160), nullable=True),
            sa.Column("reason", sa.String(1000), nullable=True),
            sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("accepted_by_caregiver_id", sa.String(), sa.ForeignKey("caregivers.id", ondelete="SET NULL"), nullable=True),
        )

    existing_indexes = {item["name"] for item in inspect(bind).get_indexes("caregiver_responsibility_transfers")}
    for name, columns in (
        ("ix_caregiver_responsibility_transfers_person_id", ["person_id"]),
        ("ix_caregiver_responsibility_transfers_source_responsibility_id", ["source_responsibility_id"]),
        ("ix_caregiver_responsibility_transfers_status", ["status"]),
    ):
        if name not in existing_indexes:
            op.create_index(name, "caregiver_responsibility_transfers", columns)


def downgrade():
    # Forward-only production repair. Dropping this table could erase audit
    # history created after repair.
    pass
