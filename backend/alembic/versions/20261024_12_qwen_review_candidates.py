"""Persist Qwen review candidates separately from the Living Care Record.

Revision ID: 20261024_12
Revises: 20261024_11

This also repeats the confidence cleanup for production databases that already
recorded 20261024_11 under the earlier consent/notification migration history.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261024_12"
down_revision = "20261024_11"
branch_labels = None
depends_on = None

_CONFIDENCE_TABLES = (
    "temporal_facts", "temporal_relations", "reasoning_memory",
    "changes", "patterns", "situations",
)


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    existing_tables = set(inspector.get_table_names())
    for table in _CONFIDENCE_TABLES:
        if table not in existing_tables:
            continue
        columns = {column["name"] for column in inspector.get_columns(table)}
        if "confidence" in columns:
            op.alter_column(
                table, "confidence", existing_type=sa.Float(),
                nullable=True, server_default=None,
            )
            op.execute(sa.text(f"UPDATE {table} SET confidence = NULL"))

    if not inspect(bind).has_table("qwen_review_candidates"):
        op.create_table(
            "qwen_review_candidates",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("person_id", sa.String(), sa.ForeignKey("persons.id", ondelete="CASCADE"), nullable=False),
            sa.Column("caregiver_id", sa.String(), sa.ForeignKey("caregivers.id", ondelete="CASCADE"), nullable=False),
            sa.Column("source_event_id", sa.String(), sa.ForeignKey("care_events.id", ondelete="CASCADE"), nullable=False),
            sa.Column("source_evidence_id", sa.String(), sa.ForeignKey("evidence.id", ondelete="SET NULL"), nullable=True),
            sa.Column("subject", sa.String(length=120), nullable=False),
            sa.Column("observation", sa.Text(), nullable=False),
            sa.Column("source_quote", sa.Text(), nullable=False),
            sa.Column("status", sa.String(length=24), nullable=False, server_default="pending_review"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("reviewed_by_caregiver_id", sa.String(), sa.ForeignKey("caregivers.id", ondelete="SET NULL"), nullable=True),
            sa.Column("review_note", sa.Text(), nullable=True),
        )
    inspector = inspect(bind)
    indexes = {item["name"] for item in inspector.get_indexes("qwen_review_candidates")}
    for name, columns in [
        ("ix_qwen_review_candidates_person_id", ["person_id"]),
        ("ix_qwen_review_candidates_caregiver_id", ["caregiver_id"]),
        ("ix_qwen_review_candidates_source_event_id", ["source_event_id"]),
        ("ix_qwen_review_candidates_source_evidence_id", ["source_evidence_id"]),
        ("ix_qwen_review_candidates_status", ["status"]),
    ]:
        if name not in indexes:
            op.create_index(name, "qwen_review_candidates", columns)


def downgrade():
    bind = op.get_bind()
    if inspect(bind).has_table("qwen_review_candidates"):
        op.drop_table("qwen_review_candidates")
