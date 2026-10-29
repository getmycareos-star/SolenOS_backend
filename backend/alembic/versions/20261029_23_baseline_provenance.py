"""Persist explicit baseline coverage and analysis-version provenance.
Revision ID: 20261029_23_baseline_provenance
Revises: 20261029_22_conflict_hist
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261029_23_baseline_provenance"
down_revision = "20261029_22_conflict_hist"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # This revision may follow a partially reconciled schema where the ORM
    # or an earlier deployed migration path already created one or both fields.
    # Preserve existing values and add only missing columns; never infer a
    # legacy baseline's historical coverage or algorithm version.
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("baselines"):
        raise RuntimeError("The baselines table must exist before baseline provenance is added.")
    existing = {column["name"] for column in inspector.get_columns("baselines")}
    columns = (
        ("coverage_status", sa.String(length=16), "unknown"),
        ("analysis_version", sa.String(length=64), "legacy-unversioned"),
    )
    for name, column_type, default in columns:
        if name not in existing:
            op.add_column(
                "baselines",
                sa.Column(name, column_type, nullable=False, server_default=default),
            )
            existing.add(name)


def downgrade() -> None:
    op.drop_column("baselines", "analysis_version")
    op.drop_column("baselines", "coverage_status")
