"""Persist correction idempotency and reconciliation execution receipts.

Revision ID: 20261115_03_correction_processing
Revises: 20261115_02_repair_responsibility_transfer_table
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261115_03_correction_processing"
down_revision = "20261115_02_repair_responsibility_transfer_table"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("corrections"):
        raise RuntimeError("Cannot add correction processing state: corrections table is missing")

    existing_columns = {column["name"] for column in inspector.get_columns("corrections")}
    columns = (
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        sa.Column("request_sha256", sa.String(length=64), nullable=True),
        sa.Column("processing_status", sa.String(length=24), nullable=False, server_default="pending"),
        sa.Column("processing_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("processing_error", sa.Text(), nullable=True),
        sa.Column("care_continuity_execution", sa.JSON(), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
    )
    for column in columns:
        if column.name not in existing_columns:
            op.add_column("corrections", column)

    existing_constraints = {
        constraint.get("name")
        for constraint in inspect(bind).get_unique_constraints("corrections")
    }
    if "uq_corrections_person_idempotency_key" not in existing_constraints:
        op.create_unique_constraint(
            "uq_corrections_person_idempotency_key",
            "corrections",
            ["person_id", "idempotency_key"],
        )


def downgrade():
    # Keep correction execution history and idempotency metadata. This migration
    # is intentionally non-destructive because clients may already rely on it.
    pass
