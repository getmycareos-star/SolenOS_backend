"""Reconcile the legacy account caregivers table with the care-record ORM.

This migration is intentionally fail-closed: if a caregivers table needs
care-record columns and already contains rows, it stops before DDL rather than
inventing caregiver/person relationships or changing existing account records.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect, text

revision = "20261022_03"
down_revision = "20261022_02"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)

    if not inspector.has_table("caregivers"):
        # The baseline migration owns creation of a missing table.
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

    # Safe only after proving the table is empty. No existing account identity or
    # foreign-key references are rewritten, and no synthetic caregiver/person rows
    # are created by this migration.
    additions = {
        "name": sa.Column("name", sa.String(), nullable=False),
        "relationship": sa.Column("relationship", sa.String(), nullable=False),
        "person_id": sa.Column("person_id", sa.String(), sa.ForeignKey("persons.id"), nullable=False),
        "timezone": sa.Column("timezone", sa.String(), nullable=False, server_default="UTC"),
    }
    if bind.dialect.name == "sqlite":
        # SQLite cannot ALTER TABLE to add a foreign-key constraint directly.
        # Rebuild is safe here because the table was proven empty above.
        with op.batch_alter_table("caregivers", recreate="always") as batch_op:
            for column_name in missing:
                batch_op.add_column(additions[column_name])
    else:
        for column_name in missing:
            op.add_column("caregivers", additions[column_name])


def downgrade():
    # Forward-only: these columns are part of the care-record ORM contract.
    # Dropping them could break deployed application code or future care data.
    pass
