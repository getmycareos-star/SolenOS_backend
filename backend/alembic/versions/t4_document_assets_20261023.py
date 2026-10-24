"""Persist uploaded document source bytes privately.

Revision ID: t4_document_assets_20261023
Revises: t3_care_handoffs_20261023
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "t4_document_assets_20261023"
down_revision = "t3_care_handoffs_20261023"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("document_assets"):
        op.create_table(
            "document_assets",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("evidence_id", sa.String(), sa.ForeignKey("evidence.id", ondelete="CASCADE"), nullable=False, unique=True),
            sa.Column("person_id", sa.String(), sa.ForeignKey("persons.id", ondelete="CASCADE"), nullable=False),
            sa.Column("uploaded_by_caregiver_id", sa.String(), sa.ForeignKey("caregivers.id"), nullable=False),
            sa.Column("original_filename", sa.String(length=255), nullable=False),
            sa.Column("content_type", sa.String(length=100), nullable=False),
            sa.Column("size_bytes", sa.Integer(), nullable=False),
            sa.Column("sha256", sa.String(length=64), nullable=False),
            sa.Column("content", sa.LargeBinary(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
    indexes = {item["name"] for item in inspect(bind).get_indexes("document_assets")}
    if "ix_document_assets_evidence_id" not in indexes:
        op.create_index("ix_document_assets_evidence_id", "document_assets", ["evidence_id"], unique=True)
    if "ix_document_assets_person_id" not in indexes:
        op.create_index("ix_document_assets_person_id", "document_assets", ["person_id"], unique=False)


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if inspector.has_table("document_assets"):
        for index in inspect(bind).get_indexes("document_assets"):
            op.drop_index(index["name"], table_name="document_assets")
        op.drop_table("document_assets")
