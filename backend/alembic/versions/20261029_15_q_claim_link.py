"""Add a normalized, same-record claim link to clarification questions.

Revision ID: 20261029_15_q_claim_link
Revises: 20261029_14_q_answer_hist
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261029_15_q_claim_link"
down_revision = "20261029_14_q_answer_hist"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("open_questions"):
        raise RuntimeError("Open questions must exist before claim links can be added.")
    columns = {column["name"] for column in inspector.get_columns("open_questions")}
    if "claim_id" not in columns:
        op.add_column(
            "open_questions",
            sa.Column(
                "claim_id",
                sa.String(),
                sa.ForeignKey("care_assertions.id", ondelete="SET NULL"),
                nullable=True,
            ),
        )
    if not any(index.get("name") == "ix_open_questions_claim_id" for index in inspect(bind).get_indexes("open_questions")):
        op.create_index("ix_open_questions_claim_id", "open_questions", ["claim_id"], unique=False)


def downgrade() -> None:
    raise RuntimeError(
        "Refusing to discard normalized claim links from clarification history."
    )
