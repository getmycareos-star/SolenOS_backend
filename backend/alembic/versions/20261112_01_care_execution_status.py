"""Persist Daily Care execution stages on capture analysis.

Revision ID: 20261112_01_care_execution_status
Revises: 20261111_03_assertion_epistemic_state
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261112_01_care_execution_status"
down_revision = "20261111_03_assertion_epistemic_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("capture_analyses"):
        return
    columns = {column["name"] for column in inspector.get_columns("capture_analyses")}
    if "execution_status" not in columns:
        op.add_column(
            "capture_analyses",
            sa.Column("execution_status", sa.JSON(), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("capture_analyses"):
        return
    columns = {column["name"] for column in inspector.get_columns("capture_analyses")}
    if "execution_status" in columns:
        op.drop_column("capture_analyses", "execution_status")
