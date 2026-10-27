"""Create missing ORM tables and permit email-less anonymous caregivers.

Revision ID: 20261022_01
Revises:
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect, text

from app.core.database import Base

revision = "20261022_01"
down_revision = "20261022_0005"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    # Create tables absent from a fresh database. create_all does not modify
    # existing tables or columns; readiness validation catches remaining drift.
    Base.metadata.create_all(bind=bind)

    inspector = inspect(bind)
    if inspector.has_table("caregivers"):
        columns = {column["name"]: column for column in inspector.get_columns("caregivers")}
        email = columns.get("email")
        if email and not email.get("nullable", True):
            op.alter_column(
                "caregivers",
                "email",
                existing_type=email["type"],
                nullable=True,
            )


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("caregivers"):
        return
    columns = {column["name"]: column for column in inspector.get_columns("caregivers")}
    email = columns.get("email")
    if not email or not email.get("nullable", True):
        return
    null_emails = bind.execute(text('SELECT COUNT(*) FROM "caregivers" WHERE email IS NULL')).scalar()
    if null_emails:
        raise RuntimeError(
            "Cannot downgrade: anonymous caregivers have NULL email values. "
            "Preserve those records or use an approved migration before retrying."
        )
    op.alter_column(
        "caregivers",
        "email",
        existing_type=email["type"],
        nullable=False,
    )
