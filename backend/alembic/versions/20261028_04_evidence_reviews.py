"""Persist versioned evidence quality, reliability, freshness and source reviews.

Revision ID: 20261028_04_evidence_reviews
Revises: 20261028_03_conflict_hist
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261028_04_evidence_reviews"
down_revision = "20261028_03_conflict_hist"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("evidence"):
        raise RuntimeError("Cannot add evidence review history: evidence table is required")
    if not inspector.has_table("evidence_review_records"):
        op.create_table(
            "evidence_review_records",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("evidence_id", sa.String(), sa.ForeignKey("evidence.id", ondelete="CASCADE"), nullable=False),
            sa.Column("person_id", sa.String(), sa.ForeignKey("persons.id", ondelete="CASCADE"), nullable=False),
            sa.Column("review_type", sa.String(length=32), nullable=False),
            sa.Column("outcome", sa.String(length=32), nullable=False),
            sa.Column("rationale", sa.Text(), nullable=False),
            sa.Column("criteria", sa.Text(), nullable=False),
            sa.Column("policy_version", sa.String(length=64), nullable=False),
            sa.Column("source_attribution_snapshot", sa.Text(), nullable=False),
            sa.Column("reviewed_by_caregiver_id", sa.String(), sa.ForeignKey("caregivers.id"), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
    for column, name in (
        ("evidence_id", "ix_evidence_review_records_evidence"),
        ("person_id", "ix_evidence_review_records_person"),
        ("review_type", "ix_evidence_review_records_type"),
    ):
        if not any(index.get("name") == name for index in inspect(bind).get_indexes("evidence_review_records")):
            op.create_index(name, "evidence_review_records", [column], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if inspector.has_table("evidence_review_records"):
        if bind.execute(sa.text("SELECT 1 FROM evidence_review_records LIMIT 1")).first():
            raise RuntimeError("Cannot downgrade while evidence review history exists.")
        op.drop_table("evidence_review_records")
