"""Add private original-file storage for evidence uploads.

Revision ID: 20261024_07
Revises: 20261024_06
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261024_07"
down_revision = "20261024_06"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if not inspect(bind).has_table("evidence_uploads"):
        op.create_table(
            "evidence_uploads",
            sa.Column("evidence_id", sa.String(), sa.ForeignKey("evidence.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("filename", sa.String(), nullable=False),
            sa.Column("mime_type", sa.String(), nullable=False),
            sa.Column("content", sa.LargeBinary(), nullable=False),
            sa.Column("size_bytes", sa.Integer(), nullable=False),
            sa.Column("sha256", sa.String(length=64), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )


def downgrade():
    bind = op.get_bind()
    if inspect(bind).has_table("evidence_uploads"):
        op.drop_table("evidence_uploads")
