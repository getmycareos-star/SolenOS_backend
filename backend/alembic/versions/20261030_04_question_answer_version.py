"""Add monotonic, unique versions to clarification answer history.

Revision ID: 20261030_04_q_answer_version
Revises: 20261030_03_q_answer_evidence
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261030_04_q_answer_version"
down_revision = "20261030_03_q_answer_evidence"
branch_labels = None
depends_on = None

INDEX_NAME = "uq_open_question_answer_version"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    table = "open_question_answer_events"
    if not inspector.has_table(table):
        raise RuntimeError("Open question answer history must exist before answer versions can be added")
    columns = {item["name"] for item in inspector.get_columns(table)}
    if "answer_version" not in columns:
        op.add_column(
            table,
            sa.Column("answer_version", sa.Integer(), nullable=True, server_default=sa.text("1")),
        )

    # Backfill a deterministic sequence for legacy events before enforcing the
    # per-question uniqueness invariant. Existing answer text/evidence is kept.
    bind.execute(sa.text("""
        WITH ranked AS (
            SELECT id,
                   ROW_NUMBER() OVER (
                       PARTITION BY question_id
                       ORDER BY created_at ASC, id ASC
                   ) AS version_number
            FROM open_question_answer_events
        )
        UPDATE open_question_answer_events AS target
        SET answer_version = ranked.version_number
        FROM ranked
        WHERE target.id = ranked.id
          AND (target.answer_version IS NULL OR target.answer_version = 1)
    """))
    op.alter_column(
        table,
        "answer_version",
        existing_type=sa.Integer(),
        nullable=False,
        server_default=sa.text("1"),
    )
    indexes = inspect(bind).get_indexes(table)
    if not any(index.get("name") == INDEX_NAME for index in indexes):
        op.create_index(
            INDEX_NAME,
            table,
            ["question_id", "answer_version"],
            unique=True,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    table = "open_question_answer_events"
    if inspector.has_table(table):
        if any(index.get("name") == INDEX_NAME for index in inspector.get_indexes(table)):
            op.drop_index(INDEX_NAME, table_name=table)
        columns = {item["name"] for item in inspector.get_columns(table)}
        if "answer_version" in columns:
            op.drop_column(table, "answer_version")
