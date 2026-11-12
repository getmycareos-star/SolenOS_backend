"""Add scoped caregiver permissions for paperwork-only family access.

Revision ID: 20261112_10_caregiver_access_scope
Revises: 20261112_09_primary_caregiver_transfer
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261112_10_caregiver_access_scope"
down_revision = "20261112_09_primary_caregiver_transfer"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("caregiver_access_grants"):
        return
    columns = {item["name"] for item in inspector.get_columns("caregiver_access_grants")}
    if "access_scope" not in columns:
        op.add_column(
            "caregiver_access_grants",
            sa.Column("access_scope", sa.String(32), nullable=False, server_default="full_record"),
        )


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if inspector.has_table("caregiver_access_grants"):
        columns = {item["name"] for item in inspect(bind).get_columns("caregiver_access_grants")}
        if "access_scope" in columns:
            op.drop_column("caregiver_access_grants", "access_scope")
