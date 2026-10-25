"""Persist source-linked Daily Capture extraction candidates and review decisions.

Revision ID: 20261025_03_capture_analysis_review
Revises: 20261025_02_accounts
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261025_03_capture_analysis_review"
down_revision = "20261025_01"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("capture_analyses"):
        op.create_table(
            "capture_analyses",
            sa.Column("id", sa.String(), primary_key=True, nullable=False),
            sa.Column("person_id", sa.String(), sa.ForeignKey("persons.id"), nullable=False),
            sa.Column("caregiver_id", sa.String(), sa.ForeignKey("caregivers.id"), nullable=False),
            sa.Column("source_event_id", sa.String(), nullable=True),
            sa.Column("source_evidence_id", sa.String(), nullable=True),
            sa.Column("source_text", sa.Text(), nullable=False),
            sa.Column("extraction_status", sa.String(length=24), nullable=False),
            sa.Column("candidates", sa.JSON(), nullable=True),
            sa.Column("review_history", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_capture_analyses_person_id", "capture_analyses", ["person_id"])
        op.create_index("ix_capture_analyses_caregiver_id", "capture_analyses", ["caregiver_id"])
        op.create_index("ix_capture_analyses_source_event_id", "capture_analyses", ["source_event_id"])
        op.create_index("ix_capture_analyses_source_evidence_id", "capture_analyses", ["source_evidence_id"])


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if inspector.has_table("capture_analyses"):
        indexes = {item["name"] for item in inspector.get_indexes("capture_analyses")}
        for name in (
            "ix_capture_analyses_source_evidence_id",
            "ix_capture_analyses_source_event_id",
            "ix_capture_analyses_caregiver_id",
            "ix_capture_analyses_person_id",
        ):
            if name in indexes:
                op.drop_index(name, table_name="capture_analyses")
        op.drop_table("capture_analyses")
