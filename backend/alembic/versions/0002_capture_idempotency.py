"""Persist capture idempotency responses.

Revision ID: 0002_capture_idempotency
Revises: 0001_initial_schema
Create Date: 2026-10-20
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0002_capture_idempotency"
down_revision: Union[str, None] = "0001_initial_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "capture_idempotency",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("person_id", sa.String(), nullable=False),
        sa.Column("caregiver_id", sa.String(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("response_status", sa.Integer(), nullable=True),
        sa.Column("response_json", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["caregiver_id"], ["caregivers.id"]),
        sa.ForeignKeyConstraint(["person_id"], ["persons.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "person_id",
            "caregiver_id",
            "idempotency_key",
            name="uq_capture_idempotency_scope_key",
        ),
    )
    op.create_index("ix_capture_idempotency_person_id", "capture_idempotency", ["person_id"])
    op.create_index("ix_capture_idempotency_caregiver_id", "capture_idempotency", ["caregiver_id"])
    op.create_index("ix_capture_idempotency_expires_at", "capture_idempotency", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_capture_idempotency_expires_at", table_name="capture_idempotency")
    op.drop_index("ix_capture_idempotency_caregiver_id", table_name="capture_idempotency")
    op.drop_index("ix_capture_idempotency_person_id", table_name="capture_idempotency")
    op.drop_table("capture_idempotency")
