"""Persist exact source evidence on every clarification answer event.

Revision ID: 20261030_03_q_answer_evidence
Revises: 20261030_02_ev_schema_fix
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261030_03_q_answer_evidence"
down_revision = "20261030_02_ev_schema_fix"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("open_question_answer_events"):
        raise RuntimeError("Open question answer history must exist before evidence links can be added")
    columns = {item["name"] for item in inspector.get_columns("open_question_answer_events")}
    if "evidence_ids" not in columns:
        op.add_column(
            "open_question_answer_events",
            sa.Column("evidence_ids", sa.Text(), nullable=False, server_default=sa.text("'[]'")),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if inspector.has_table("open_question_answer_events"):
        columns = {item["name"] for item in inspector.get_columns("open_question_answer_events")}
        if "evidence_ids" in columns:
            op.drop_column("open_question_answer_events", "evidence_ids")
