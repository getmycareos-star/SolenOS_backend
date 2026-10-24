"""Make legacy caregiver account fields nullable for anonymous caregivers.

This forward-only migration preserves existing values and relaxes only NOT NULL
constraints on legacy fields that the current care-record ORM does not require.
"""
from alembic import op
from sqlalchemy import inspect

revision = "20261024_04"
down_revision = "20261023_05"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("caregivers"):
        raise RuntimeError("Cannot reconcile caregiver constraints: caregivers table is absent.")
    columns = {column["name"]: column for column in inspector.get_columns("caregivers")}
    for name in ("password_hash", "display_name"):
        column = columns.get(name)
        if column and not column.get("nullable", True) and not column.get("primary_key"):
            op.alter_column("caregivers", name,
                            existing_type=column["type"], nullable=True)
            columns = {item["name"]: item for item in inspect(bind).get_columns("caregivers")}


def downgrade():
    raise RuntimeError("Forward-only migration; downgrade requires a reviewed data-preservation plan.")
