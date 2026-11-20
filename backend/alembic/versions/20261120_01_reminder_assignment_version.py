"""Persist accountable reminder ownership and optimistic concurrency.

Revision ID: 20261120_01_reminder_assignment_version
Revises: 20261119_01_document_statement_context
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261120_01_reminder_assignment_version"
down_revision = "20261119_01_document_statement_context"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("care_reminders"):
        raise RuntimeError("Cannot add reminder ownership: care_reminders is missing")
    columns = {column["name"] for column in inspector.get_columns("care_reminders")}
    if "assigned_to_caregiver_id" not in columns:
        op.add_column(
            "care_reminders",
            sa.Column(
                "assigned_to_caregiver_id",
                sa.String(),
                sa.ForeignKey("caregivers.id", ondelete="SET NULL"),
                nullable=True,
            ),
        )
        # Existing reminders remain assigned to their original creator.
        op.execute(
            "UPDATE care_reminders "
            "SET assigned_to_caregiver_id = created_by_caregiver_id "
            "WHERE assigned_to_caregiver_id IS NULL"
        )
    if "version" not in columns:
        op.add_column(
            "care_reminders",
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        )
    indexes = {index["name"] for index in inspect(bind).get_indexes("care_reminders")}
    if "ix_care_reminders_assigned_to_caregiver_id" not in indexes:
        op.create_index(
            "ix_care_reminders_assigned_to_caregiver_id",
            "care_reminders",
            ["assigned_to_caregiver_id"],
            unique=False,
        )


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if inspector.has_table("care_reminders"):
        indexes = {index["name"] for index in inspect(bind).get_indexes("care_reminders")}
        if "ix_care_reminders_assigned_to_caregiver_id" in indexes:
            op.drop_index("ix_care_reminders_assigned_to_caregiver_id", table_name="care_reminders")
        columns = {column["name"] for column in inspect(bind).get_columns("care_reminders")}
        if "version" in columns:
            op.drop_column("care_reminders", "version")
        if "assigned_to_caregiver_id" in columns:
            op.drop_column("care_reminders", "assigned_to_caregiver_id")
