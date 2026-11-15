"""Recheck and repair caregiver responsibility-transfer storage after stamped revisions.

Revision ID: 20261115_05_repair_caregiver_responsibility_transfers
Revises: 20261115_04_correction_processing_lease
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261115_05_repair_caregiver_responsibility_transfers"
down_revision = "20261115_04_correction_processing_lease"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)

    # Revision 20261115_02 repairs databases when first applied. Production
    # readiness later found a database already stamped beyond that revision
    # while this table was still absent. A new forward-only revision is
    # required because Alembic will not rerun an already-stamped migration.
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
    # This repair is intentionally forward-only. Never drop existing caregiver
    # responsibility-transfer history during a rollback.
    pass
