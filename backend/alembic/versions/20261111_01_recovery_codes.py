"""Persist one-time offline recovery codes for caregiver accounts.

Revision ID: 20261111_01_recovery_codes
Revises: 20261107_05_appt_idempotency
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261111_01_recovery_codes"
down_revision = "20261107_05_appt_idempotency"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("recovery_code_sets"):
        op.create_table(
            "recovery_code_sets",
            sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
            sa.Column("account_id", sa.String(length=36), sa.ForeignKey("caregiver_accounts.id", ondelete="CASCADE"), nullable=False),
            sa.Column("code_hash", sa.String(length=64), nullable=False),
            sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("code_hash", name="uq_recovery_code_sets_code_hash"),
        )
    indexes = {item["name"] for item in inspect(bind).get_indexes("recovery_code_sets")}
    if "ix_recovery_code_sets_account_id" not in indexes:
        op.create_index("ix_recovery_code_sets_account_id", "recovery_code_sets", ["account_id"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    if inspect(bind).has_table("recovery_code_sets"):
        op.drop_table("recovery_code_sets")
