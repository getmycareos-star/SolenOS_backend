"""Persist answerer intent and explicit clarification necessity.

Revision ID: 20261029_16_q_intent
Revises: 20261029_15_q_claim_link
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261029_16_q_intent"
down_revision = "20261029_15_q_claim_link"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("open_questions"):
        raise RuntimeError("Open questions must exist before intent metadata can be added.")
    columns = {column["name"] for column in inspector.get_columns("open_questions")}
    additions = (
        ("intended_answerer_role", sa.String(length=40), True, None),
        ("intended_answerer_label", sa.Text(), True, None),
        ("necessity", sa.String(length=20), False, "not_assessed"),
        ("necessity_rationale", sa.Text(), True, None),
    )
    for name, type_, nullable, default in additions:
        if name not in columns:
            op.add_column(
                "open_questions",
                sa.Column(name, type_, nullable=nullable, server_default=default),
            )
    if "necessity" in {column["name"] for column in inspect(bind).get_columns("open_questions")}:
        op.alter_column("open_questions", "necessity", server_default=None)


def downgrade() -> None:
    raise RuntimeError(
        "Refusing to discard clarification answerer intent and necessity assessments."
    )
