"""Relax legacy NOT NULL columns on caregivers that the care-record ORM never writes.

Anonymous caregivers are inserted without credentials. A legacy account-era
column (observed: password_hash) is NOT NULL in production and makes
POST /api/v1/sessions/anonymous fail with a NotNullViolation.

Only drops NOT NULL (no data is changed or removed) and only on columns that
are absent from the ORM model and have no server default. Idempotent.

Revision ID: 20261024_04
Revises: 20261022_03
"""
from alembic import op
from sqlalchemy import inspect

revision = "20261024_04"
down_revision = "20261022_03"
branch_labels = None
depends_on = None

ORM_COLUMNS = {"id", "name", "email", "relationship", "person_id", "timezone", "created_at"}


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("caregivers"):
        return
    for column in inspector.get_columns("caregivers"):
        name = column["name"]
        if name in ORM_COLUMNS or column.get("nullable", True) or column.get("default") is not None:
            continue
        if column.get("primary_key"):
            continue
        op.alter_column("caregivers", name, existing_type=column["type"], nullable=True)


def downgrade():
    # Forward-only: re-adding NOT NULL would fail once anonymous caregivers exist.
    pass
