"""Persist explicit domain observation coverage for defensible longitudinal change.
Revision ID: 20261029_06_observation_coverage
Revises: 20261029_05_evidence_source_immutable
"""
from alembic import op
import sqlalchemy as sa

revision = "20261029_06_observation_coverage"
down_revision = "20261029_05_evidence_immut"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "observation_coverage",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("person_id", sa.String(), sa.ForeignKey("persons.id"), nullable=False),
        sa.Column("subject_type", sa.String(length=120), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("coverage_status", sa.String(length=16), nullable=False),
        sa.Column("source_evidence_ids", sa.JSON(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("recorded_by_caregiver_id", sa.String(), sa.ForeignKey("caregivers.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_observation_coverage_person_id", "observation_coverage", ["person_id"])
    op.create_index("ix_observation_coverage_subject_type", "observation_coverage", ["subject_type"])
    op.create_index("ix_observation_coverage_window_start", "observation_coverage", ["window_start"])
    op.create_index("ix_observation_coverage_window_end", "observation_coverage", ["window_end"])
    op.create_index("ix_observation_coverage_coverage_status", "observation_coverage", ["coverage_status"])


def downgrade() -> None:
    op.drop_index("ix_observation_coverage_coverage_status", table_name="observation_coverage")
    op.drop_index("ix_observation_coverage_window_end", table_name="observation_coverage")
    op.drop_index("ix_observation_coverage_window_start", table_name="observation_coverage")
    op.drop_index("ix_observation_coverage_subject_type", table_name="observation_coverage")
    op.drop_index("ix_observation_coverage_person_id", table_name="observation_coverage")
    op.drop_table("observation_coverage")
