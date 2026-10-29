"""Persist coverage provenance directly on longitudinal change assessments.
Revision ID: 20261029_11_cov_prov
Revises: 20261029_10_coverage_immutable
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261029_11_cov_prov"
down_revision = "20261029_10_coverage_immutable"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("changes"):
        raise RuntimeError("The changes table must exist before coverage provenance can be added.")

    # These columns were introduced in some existing schema paths before this
    # revision was formalized. Reconcile additively instead of failing on a
    # duplicate column; never drop or rewrite already persisted assessments.
    existing = {column["name"] for column in inspector.get_columns("changes")}
    columns = (
        ("baseline_coverage_status", sa.String(length=16)),
        ("current_coverage_status", sa.String(length=16)),
        ("baseline_coverage_fraction", sa.Float()),
        ("current_coverage_fraction", sa.Float()),
    )
    for name, column_type in columns:
        if name not in existing:
            op.add_column("changes", sa.Column(name, column_type, nullable=True))
            existing.add(name)


def downgrade() -> None:
    # Preserve audit provenance; removing it would make older assessments less interpretable.
    raise RuntimeError("Refusing to remove persisted change coverage provenance.")
