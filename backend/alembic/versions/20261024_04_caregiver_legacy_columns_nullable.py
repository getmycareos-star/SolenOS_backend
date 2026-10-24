"""Make the legacy caregivers.password_hash column nullable.

The care-record ORM does not model or write password_hash. Existing production
anonymous-session inserts therefore fail when this legacy account-era column
is NOT NULL. Limit this migration to the confirmed offending column; do not
relax constraints on unrelated legacy columns.

Revision ID: 20261024_04
Revises: 20261023_05
"""
from alembic import op
from sqlalchemy import inspect, text

revision = "20261024_04"
down_revision = "20261023_05"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("caregivers"):
        return

    columns = {column["name"]: column for column in inspector.get_columns("caregivers")}
    column = columns.get("password_hash")
    if column is None or column.get("nullable", True) or column.get("primary_key"):
        return

    op.alter_column(
        "caregivers",
        "password_hash",
        existing_type=column["type"],
        nullable=True,
    )


def downgrade():
    # Reapply NOT NULL only when the existing data satisfies it. Never delete
    # or rewrite credentials to force a downgrade.
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("caregivers"):
        return

    columns = {column["name"]: column for column in inspector.get_columns("caregivers")}
    column = columns.get("password_hash")
    if column is None or not column.get("nullable", True) or column.get("primary_key"):
        return

    null_count = bind.execute(
        text("SELECT COUNT(*) FROM caregivers WHERE password_hash IS NULL")
    ).scalar_one()
    if null_count:
        raise RuntimeError(
            "Cannot downgrade 20261024_04: caregivers.password_hash contains "
            f"{null_count} NULL row(s). Preserve the data and keep this migration applied."
        )

    op.alter_column(
        "caregivers",
        "password_hash",
        existing_type=column["type"],
        nullable=False,
    )
