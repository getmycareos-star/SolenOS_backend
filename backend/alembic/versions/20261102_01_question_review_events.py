"""Append-only review decisions for clarification answer versions."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261102_01_question_review_events"
down_revision = "20261030_05_history_merge"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    table = "open_question_review_events"
    required_columns = {
        "id", "question_id", "person_id", "answer_event_id", "answer_version",
        "decision", "rationale", "reviewer_caregiver_id", "claim_assessment_id", "created_at",
    }
    if not inspector.has_table(table):
        op.create_table(
            table,
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("question_id", sa.String(), sa.ForeignKey("open_questions.id", ondelete="CASCADE"), nullable=False),
            sa.Column("person_id", sa.String(), sa.ForeignKey("persons.id", ondelete="CASCADE"), nullable=False),
            sa.Column("answer_event_id", sa.String(), sa.ForeignKey("open_question_answer_events.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("answer_version", sa.Integer(), nullable=False),
            sa.Column("decision", sa.String(length=32), nullable=False),
            sa.Column("rationale", sa.Text(), nullable=False),
            sa.Column("reviewer_caregiver_id", sa.String(), sa.ForeignKey("caregivers.id"), nullable=False),
            sa.Column("claim_assessment_id", sa.String(), sa.ForeignKey("evidence_claim_assessments.id"), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("question_id", "answer_version", name="uq_open_question_review_answer_version"),
        )
    else:
        actual = {column["name"] for column in inspector.get_columns(table)}
        missing = required_columns - actual
        if missing:
            raise RuntimeError(
                "Existing open_question_review_events table is incomplete; refusing incompatible schema. "
                f"Missing columns: {sorted(missing)}"
            )
        uniques = inspector.get_unique_constraints(table)
        indexes = inspector.get_indexes(table)
        if not (
            any(set(item.get("column_names") or []) == {"question_id", "answer_version"} for item in uniques)
            or any(item.get("unique") and set(item.get("column_names") or []) == {"question_id", "answer_version"} for item in indexes)
        ):
            raise RuntimeError("Clarification review history lacks one-decision-per-answer-version enforcement")

    indexes = {item["name"] for item in inspect(bind).get_indexes(table)}
    for name, columns in (
        ("ix_open_question_review_events_question_id", ["question_id"]),
        ("ix_open_question_review_events_person_id", ["person_id"]),
        ("ix_open_question_review_person_question", ["person_id", "question_id", "created_at"]),
    ):
        if name not in indexes:
            op.create_index(name, table, columns)


def downgrade() -> None:
    raise RuntimeError("Refusing to drop append-only clarification review history.")
