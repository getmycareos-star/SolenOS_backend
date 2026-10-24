"""Persist short-lived, one-time share-target intake.

Revision ID: 20261024_09
Revises: 20261024_08
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261024_09"
down_revision = "20261024_08"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if not inspect(bind).has_table("share_intakes"):
        op.create_table(
            "share_intakes",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("payload_json", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        )
    inspector = inspect(bind)
    if inspector.has_table("share_intakes"):
        existing = {item["name"] for item in inspector.get_indexes("share_intakes")}
        if "ix_share_intakes_expires_at" not in existing:
            op.create_index("ix_share_intakes_expires_at", "share_intakes", ["expires_at"])


def downgrade():
    bind = op.get_bind()
    if inspect(bind).has_table("share_intakes"):
        op.drop_table("share_intakes")
