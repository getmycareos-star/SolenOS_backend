"""Add a recoverable processing lease to correction execution.

Revision ID: 20261115_04_correction_processing_lease
Revises: 20261115_03_correction_processing
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261115_04_correction_processing_lease"
down_revision = "20261115_03_correction_processing"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("corrections"):
        raise RuntimeError("Cannot add correction lease: corrections table is missing")
    columns = {column["name"] for column in inspector.get_columns("corrections")}
    if "processing_started_at" not in columns:
        op.add_column(
            "corrections",
            sa.Column("processing_started_at", sa.DateTime(timezone=True), nullable=True),
        )
    indexes = {index["name"] for index in inspect(bind).get_indexes("corrections")}
    if "ix_corrections_processing_started_at" not in indexes:
        op.create_index(
            "ix_corrections_processing_started_at",
            "corrections",
            ["processing_started_at"],
            unique=False,
        )


def downgrade():
    # Retain execution metadata to avoid making pending corrections unrecoverable.
    pass
