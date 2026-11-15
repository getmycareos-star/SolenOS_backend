"""Add retry scheduling for durable correction processing.

Revision ID: 20261115_05_correction_retry_schedule
Revises: 20261115_04_correction_processing_lease
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261115_05_correction_retry_schedule"
down_revision = "20261115_04_correction_processing_lease"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("corrections"):
        raise RuntimeError("Cannot add correction retry schedule: corrections table is missing")
    columns = {column["name"] for column in inspector.get_columns("corrections")}
    if "next_retry_at" not in columns:
        op.add_column("corrections", sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True))
    indexes = {index["name"] for index in inspect(bind).get_indexes("corrections")}
    if "ix_corrections_next_retry_at" not in indexes:
        op.create_index("ix_corrections_next_retry_at", "corrections", ["next_retry_at"], unique=False)


def downgrade():
    # Preserve retry scheduling metadata for already accepted corrections.
    pass
