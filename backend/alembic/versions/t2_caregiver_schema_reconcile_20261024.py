"""Reconcile legacy caregiver columns without guessing existing identity mappings.

Revision ID: t2_caregiver_schema_reconcile_20261024
Revises: t2_delivery_attempts_20261023

The migration is deliberately fail-closed: if the legacy caregivers table needs
care-record columns but already contains rows, stop before DDL. Do not infer a
person relationship from account identity data.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect, text

revision = "t2_caregiver_schema_reconcile_20261024"
down_revision = "t2_delivery_attempts_20261023"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("caregivers"):
        return

    columns = {column["name"] for column in inspector.get_columns("caregivers")}
    required = {"name", "relationship", "person_id", "timezone"}
    missing = sorted(required - columns)
    if not missing:
        return

    row_count = bind.execute(text("SELECT COUNT(*) FROM caregivers")).scalar_one()
    if row_count:
        raise RuntimeError(
            "Caregiver schema reconciliation requires a reviewed identity mapping: "
            f"caregivers has {row_count} existing rows and is missing {', '.join(missing)}. "
            "No columns were changed. Do not infer person relationships from account fields."
        )

    # The table is empty, so required columns can be added without fabricating
    # identity mappings or rewriting existing account/care records.
    additions = {
        "name": sa.Column("name", sa.String(), nullable=False),
        "relationship": sa.Column("relationship", sa.String(), nullable=False),
        "person_id": sa.Column(
            "person_id",
            sa.String(),
            sa.ForeignKey("persons.id"),
            nullable=False,
        ),
        "timezone": sa.Column("timezone", sa.String(), nullable=False, server_default="UTC"),
    }
    for column_name in missing:
        op.add_column("caregivers", additions[column_name])


def downgrade():
    # Forward-only: these columns are part of the care-record ORM contract.
    # Dropping them could break deployed application code or future care data.
    pass
