"""Repair a missing caregiver responsibility transfer table even when the earlier revision is stamped.

Revision ID: 20261115_02_repair_responsibility_transfer_table
Revises: 20261115_01_situation_states
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261115_02_repair_responsibility_transfer_table"
down_revision = "20261115_01_situation_states"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)

    # The original caregiver-access migration creates this table. Production
    # readiness has exposed a database where the revision is present but the
    # table is absent. This forward-only repair makes that partial schema state
    # recoverable without rewriting or deleting any existing care data.
    if not inspector.has_table("caregiver_responsibility_transfers"):
        required_parent_tables = {
            "caregiver_responsibilities",
            "caregiver_profiles",
            "caregivers",
            "persons",
        }
        missing_parents = sorted(
            name for name in required_parent_tables if not inspector.has_table(name)
        )
        if missing_parents:
            raise RuntimeError(
                "Cannot repair caregiver_responsibility_transfers; "
                f"missing parent tables: {missing_parents}"
            )

        op.create_table(
            "caregiver_responsibility_transfers",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column(
                "person_id",
                sa.String(),
                sa.ForeignKey("persons.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "source_responsibility_id",
                sa.String(),
                sa.ForeignKey("caregiver_responsibilities.id", ondelete="RESTRICT"),
                nullable=False,
            ),
            sa.Column(
                "from_caregiver_profile_id",
                sa.String(),
                sa.ForeignKey("caregiver_profiles.id", ondelete="RESTRICT"),
                nullable=False,
            ),
            sa.Column(
                "to_caregiver_profile_id",
                sa.String(),
                sa.ForeignKey("caregiver_profiles.id", ondelete="RESTRICT"),
                nullable=False,
            ),
            sa.Column(
                "created_by_caregiver_id",
                sa.String(),
                sa.ForeignKey("caregivers.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("related_record_type", sa.String(40), nullable=True),
            sa.Column("related_record_id", sa.String(160), nullable=True),
            sa.Column("reason", sa.String(1000), nullable=True),
            sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "accepted_by_caregiver_id",
                sa.String(),
                sa.ForeignKey("caregivers.id", ondelete="SET NULL"),
                nullable=True,
            ),
        )

    existing_indexes = {
        item["name"]
        for item in inspect(bind).get_indexes("caregiver_responsibility_transfers")
    }
    for name, columns in (
        ("ix_caregiver_responsibility_transfers_person_id", ["person_id"]),
        ("ix_caregiver_responsibility_transfers_source_responsibility_id", ["source_responsibility_id"]),
        ("ix_caregiver_responsibility_transfers_status", ["status"]),
    ):
        if name not in existing_indexes:
            op.create_index(name, "caregiver_responsibility_transfers", columns)


def downgrade():
    # Deliberately non-destructive: the table may have existed before this
    # repair revision and may contain accepted responsibility-transfer history.
    pass
