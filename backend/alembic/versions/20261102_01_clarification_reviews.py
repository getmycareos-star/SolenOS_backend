"""Persist append-only clarification review decisions.

Revision ID: 20261102_01_clarification_reviews
Revises: 20261030_05_history_merge
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261102_01_clarification_reviews"
down_revision = "20261030_05_history_merge"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("open_questions") or not inspector.has_table("open_question_answer_events"):
        raise RuntimeError("Clarification questions and answer history must exist before review decisions are added")
    if not inspector.has_table("clarification_review_events"):
        op.create_table(
            "clarification_review_events",
            sa.Column("id", sa.String(), primary_key=True, nullable=False),
            sa.Column("question_id", sa.String(), sa.ForeignKey("open_questions.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("answer_event_id", sa.String(), sa.ForeignKey("open_question_answer_events.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("answer_version", sa.Integer(), nullable=False),
            sa.Column("person_id", sa.String(), sa.ForeignKey("persons.id", ondelete="CASCADE"), nullable=False),
            sa.Column("decision", sa.String(length=32), nullable=False),
            sa.Column("rationale", sa.Text(), nullable=False),
            sa.Column("reviewed_by_caregiver_id", sa.String(), sa.ForeignKey("caregivers.id"), nullable=False),
            sa.Column("resulting_assertion_id", sa.String(), sa.ForeignKey("care_assertions.id", ondelete="RESTRICT"), nullable=True),
            sa.Column("reconciliation_status", sa.String(length=32), nullable=False),
            sa.Column("evidence_review_ids", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("answer_event_id", name="uq_clarification_review_answer_event"),
        )
    indexes = {item["name"] for item in inspect(bind).get_indexes("clarification_review_events")}
    if "ix_clarification_review_events_question_id" not in indexes:
        op.create_index("ix_clarification_review_events_question_id", "clarification_review_events", ["question_id"])
    if "ix_clarification_review_events_answer_event_id" not in indexes:
        op.create_index("ix_clarification_review_events_answer_event_id", "clarification_review_events", ["answer_event_id"])
    if "ix_clarification_review_events_person_id" not in indexes:
        op.create_index("ix_clarification_review_events_person_id", "clarification_review_events", ["person_id"])
    if "ix_clarification_review_person_created" not in indexes:
        op.create_index("ix_clarification_review_person_created", "clarification_review_events", ["person_id", "created_at"])


def downgrade() -> None:
    bind = op.get_bind()
    if inspect(bind).has_table("clarification_review_events"):
        op.drop_table("clarification_review_events")
