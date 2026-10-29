"""Expose whether baseline history was complete or bounded.
Revision ID: 20261029_24_baseline_history
Revises: 20261029_23_baseline_provenance
"""
from alembic import op
import sqlalchemy as sa

revision = "20261029_24_baseline_history"
down_revision = "20261029_23_baseline_provenance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Legacy baseline rows have no trustworthy completeness marker. Do not
    # infer completeness from row count, density, or explanatory prose.
    op.add_column(
        "baselines",
        sa.Column(
            "history_completeness",
            sa.String(length=16),
            nullable=False,
            server_default="unknown",
        ),
    )


def downgrade() -> None:
    op.drop_column("baselines", "history_completeness")
