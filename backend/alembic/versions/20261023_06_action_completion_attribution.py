"""Persist completion time and caregiver attribution for actions.

Revision ID: 20261023_06
Revises: 20261023_05
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261023_06"
down_revision = "20261023_05"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("actions"):
        return
    existing = {column["name"] for column in inspector.get_columns("actions")}
    if "completed_at" not in existing:
        op.add_column("actions", sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True))
    if "completed_by_caregiver_id" not in existing:
        op.add_column("actions", sa.Column("completed_by_caregiver_id", sa.String(), sa.ForeignKey("caregivers.id"), nullable=True))
    if "completion_notes" not in existing:
        op.add_column("actions", sa.Column("completion_notes", sa.Text(), nullable=True))
    indexes = {item["name"] for item in inspect(bind).get_indexes("actions")}
    if "ix_actions_completed_at" not in indexes:
        op.create_index("ix_actions_completed_at", "actions", ["completed_at"])


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("actions"):
        return
    indexes = {item["name"] for item in inspector.get_indexes("actions")}
    if "ix_actions_completed_at" in indexes:
        op.drop_index("ix_actions_completed_at", table_name="actions")
    existing = {column["name"] for column in inspect(bind).get_columns("actions")}
    for name in ("completion_notes", "completed_by_caregiver_id", "completed_at"):
        if name in existing:
            op.drop_column("actions", name)
