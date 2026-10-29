"""Persist explicit domain observation coverage for defensible longitudinal change.
Revision ID: 20261029_06_observation_coverage
Revises: 20261029_05_evidence_immut
"""
from alembic import op
import sqlalchemy as sa

revision = "20261029_06_observation_coverage"
down_revision = "20261029_05_evidence_immut"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    required_columns = {
        "id", "person_id", "subject_type", "window_start", "window_end",
        "coverage_status", "source_evidence_ids", "rationale",
        "recorded_by_caregiver_id", "created_at",
    }
    if not inspector.has_table("observation_coverage"):
        op.create_table(
            "observation_coverage",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("person_id", sa.String(), sa.ForeignKey("persons.id"), nullable=False),
            sa.Column("subject_type", sa.String(length=120), nullable=False),
            sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
            sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
            sa.Column("coverage_status", sa.String(length=16), nullable=False),
            sa.Column("source_evidence_ids", sa.Text(), nullable=False),
            sa.Column("rationale", sa.Text(), nullable=False),
            sa.Column("recorded_by_caregiver_id", sa.String(), sa.ForeignKey("caregivers.id"), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
        inspector = sa.inspect(bind)
    else:
        existing_columns = {column["name"] for column in inspector.get_columns("observation_coverage")}
        missing_columns = required_columns - existing_columns
        if missing_columns:
            raise RuntimeError(
                "Existing observation_coverage table is incomplete; refusing to silently accept or destructively rewrite it. "
                f"Missing columns: {sorted(missing_columns)}"
            )

    # Legacy/staged databases may already have this table. Reconcile indexes without
    # duplicating them, and never drop or rewrite recorded coverage data.
    existing_indexes = {index["name"] for index in inspector.get_indexes("observation_coverage")}
    for index_name, column_name in (
        ("ix_observation_coverage_person_id", "person_id"),
        ("ix_observation_coverage_subject_type", "subject_type"),
        ("ix_observation_coverage_window_start", "window_start"),
        ("ix_observation_coverage_window_end", "window_end"),
        ("ix_observation_coverage_coverage_status", "coverage_status"),
    ):
        if index_name not in existing_indexes:
            op.create_index(index_name, "observation_coverage", [column_name])


def downgrade() -> None:
    raise RuntimeError(
        "Refusing to drop observation coverage records during downgrade; preserve longitudinal evidence and audit history."
    )
