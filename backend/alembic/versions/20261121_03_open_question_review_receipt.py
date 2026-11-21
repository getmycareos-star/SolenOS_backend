"""Persist clarification review reconciliation receipts.

Revision ID: 20261121_03_open_question_review_receipt
Revises: 20261121_02_evidence_review_idem
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261121_03_open_question_review_receipt"
down_revision = "20261121_02_evidence_review_idem"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    table = "open_question_review_events"
    if not inspector.has_table(table):
        raise RuntimeError(
            "open_question_review_events must exist before reconciliation receipts are added"
        )
    columns = {column["name"] for column in inspector.get_columns(table)}
    if "care_continuity_execution" not in columns:
        op.add_column(
            table,
            sa.Column("care_continuity_execution", sa.JSON(), nullable=True),
        )


def downgrade() -> None:
    # Review receipts are part of the append-only clarification decision record.
    # Dropping them would destroy evidence of the reconciliation performed.
    raise RuntimeError("Refusing to drop persisted clarification reconciliation receipts.")
