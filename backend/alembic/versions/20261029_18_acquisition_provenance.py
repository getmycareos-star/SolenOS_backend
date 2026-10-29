"""Add explicit acquisition and transfer provenance to evidence.

Revision ID: 20261029_18_acq_provenance
Revises: 20261029_17_pattern_metrics
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261029_18_acq_provenance"
down_revision = "20261029_17_pattern_metrics"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("evidence"):
        raise RuntimeError("The evidence table must exist before acquisition provenance is added.")
    existing = {column["name"] for column in inspector.get_columns("evidence")}
    columns = (
        sa.Column("reported_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("collection_method", sa.String(length=80), nullable=True),
        sa.Column("origin_device_ref", sa.String(length=160), nullable=True),
        sa.Column("transfer_source", sa.String(length=160), nullable=True),
        sa.Column("transfer_reference", sa.Text(), nullable=True),
    )
    for column in columns:
        if column.name not in existing:
            op.add_column("evidence", column)


def downgrade() -> None:
    raise RuntimeError(
        "Refusing to discard evidence acquisition and transfer provenance during downgrade."
    )
