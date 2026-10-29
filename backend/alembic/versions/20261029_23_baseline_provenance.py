"""Persist explicit baseline coverage and analysis-version provenance.
Revision ID: 20261029_23_baseline_provenance
Revises: 20261029_22_conflict_hist
"""
from alembic import op
import sqlalchemy as sa

revision = "20261029_23_baseline_provenance"
down_revision = "20261029_22_conflict_hist"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing baselines cannot be retroactively assigned a coverage state or
    # algorithm version that their original rows did not record.
    op.add_column(
        "baselines",
        sa.Column(
            "coverage_status",
            sa.String(length=16),
            nullable=False,
            server_default="unknown",
        ),
    )
    op.add_column(
        "baselines",
        sa.Column(
            "analysis_version",
            sa.String(length=64),
            nullable=False,
            server_default="legacy-unversioned",
        ),
    )


def downgrade() -> None:
    op.drop_column("baselines", "analysis_version")
    op.drop_column("baselines", "coverage_status")
