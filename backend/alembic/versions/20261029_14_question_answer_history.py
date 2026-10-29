"""Add immutable, attributed answer history for clarification questions.

Revision ID: 20261029_14_question_answer_history
Revises: 20261029_13_conflict_scope
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261029_14_question_answer_history"
down_revision = "20261029_13_conflict_scope"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("open_questions"):
        raise RuntimeError("Open questions must exist before answer history can be added.")
    question_columns = {column["name"] for column in inspector.get_columns("open_questions")}
    if "answered_by_caregiver_id" not in question_columns:
        op.add_column(
            "open_questions",
            sa.Column("answered_by_caregiver_id", sa.String(), sa.ForeignKey("caregivers.id"), nullable=True),
        )

    if not inspector.has_table("open_question_answer_events"):
        op.create_table(
            "open_question_answer_events",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("question_id", sa.String(), sa.ForeignKey("open_questions.id", ondelete="CASCADE"), nullable=False),
            sa.Column("person_id", sa.String(), sa.ForeignKey("persons.id", ondelete="CASCADE"), nullable=False),
            sa.Column("answer", sa.Text(), nullable=False),
            sa.Column("answer_provenance", sa.Text(), nullable=False),
            sa.Column("answered_by_caregiver_id", sa.String(), sa.ForeignKey("caregivers.id"), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
    for name, column in (
        ("ix_open_question_answer_events_question_id", "question_id"),
        ("ix_open_question_answer_events_person_id", "person_id"),
        ("ix_open_question_answer_events_created_at", "created_at"),
    ):
        if not any(index.get("name") == name for index in inspect(bind).get_indexes("open_question_answer_events")):
            op.create_index(name, "open_question_answer_events", [column], unique=False)

    if bind.dialect.name == "postgresql":
        op.execute("""
            CREATE OR REPLACE FUNCTION prevent_open_question_answer_event_mutation()
            RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'Clarification answer history is append-only and cannot be updated or deleted';
            END;
            $$ LANGUAGE plpgsql;
        """)
        op.execute("""
            DROP TRIGGER IF EXISTS trg_open_question_answer_events_immutable
            ON open_question_answer_events;
        """)
        op.execute("""
            CREATE TRIGGER trg_open_question_answer_events_immutable
            BEFORE UPDATE OR DELETE ON open_question_answer_events
            FOR EACH ROW EXECUTE FUNCTION prevent_open_question_answer_event_mutation();
        """)
    elif bind.dialect.name == "sqlite":
        op.execute("""
            CREATE TRIGGER IF NOT EXISTS trg_open_question_answer_events_immutable_update
            BEFORE UPDATE ON open_question_answer_events
            BEGIN
                SELECT RAISE(ABORT, 'Clarification answer history is append-only and cannot be updated or deleted');
            END;
        """)
        op.execute("""
            CREATE TRIGGER IF NOT EXISTS trg_open_question_answer_events_immutable_delete
            BEFORE DELETE ON open_question_answer_events
            BEGIN
                SELECT RAISE(ABORT, 'Clarification answer history is append-only and cannot be updated or deleted');
            END;
        """)


def downgrade() -> None:
    raise RuntimeError(
        "Refusing to discard attributed clarification-answer history."
    )
