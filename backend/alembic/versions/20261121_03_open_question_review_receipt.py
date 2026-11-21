"""Persist clarification review reconciliation receipts.

Revision ID: 20261121_03_open_question_review_receipt
Revises: 20261121_02_evidence_review_idempotency
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
    # Preserve any durable reconciliation receipts. If none have been written,
    # the nullable column may be removed safely; if receipts exist, leave the
    # column in place rather than destroy append-only decision evidence.
    bind = op.get_bind()
    inspector = inspect(bind)
    table = "open_question_review_events"
    if not inspector.has_table(table):
        return
    columns = {column["name"] for column in inspector.get_columns(table)}
    if "care_continuity_execution" not in columns:
        return
    receipt_count = bind.execute(
        sa.text(f"SELECT COUNT(*) FROM {table} WHERE care_continuity_execution IS NOT NULL")
    ).scalar_one()
    if receipt_count:
        return
    op.drop_column(table, "care_continuity_execution")
