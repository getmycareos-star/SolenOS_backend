"""Persist coverage provenance directly on longitudinal change assessments.
Revision ID: 20261029_11_change_coverage_provenance
Revises: 20261029_10_coverage_immutable
"""
from alembic import op
import sqlalchemy as sa

revision = "20261029_11_change_coverage_provenance"
down_revision = "20261029_10_coverage_immutable"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("changes", sa.Column("baseline_coverage_status", sa.String(length=16), nullable=True))
    op.add_column("changes", sa.Column("current_coverage_status", sa.String(length=16), nullable=True))
    op.add_column("changes", sa.Column("baseline_coverage_fraction", sa.Float(), nullable=True))
    op.add_column("changes", sa.Column("current_coverage_fraction", sa.Float(), nullable=True))


def downgrade() -> None:
    # Preserve audit provenance; removing it would make older assessments less interpretable.
    raise RuntimeError("Refusing to remove persisted change coverage provenance.")
