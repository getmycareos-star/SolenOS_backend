"""Persist expiring, authenticated privacy export archives.

Revision ID: 20261111_02_privacy_exports
Revises: 20261111_01_recovery_codes
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261111_02_privacy_exports"
down_revision = "20261111_01_recovery_codes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("privacy_export_artifacts"):
        op.create_table(
            "privacy_export_artifacts",
            sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
            sa.Column(
                "request_id", sa.String(length=36),
                sa.ForeignKey("data_subject_requests.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("person_id", sa.String(length=255), nullable=False),
            sa.Column("caregiver_id", sa.String(length=255), nullable=False),
            sa.Column("filename", sa.String(length=255), nullable=False),
            sa.Column("content", sa.LargeBinary(), nullable=False),
            sa.Column("size_bytes", sa.Integer(), nullable=False),
            sa.Column("sha256", sa.String(length=64), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("downloaded_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("request_id", name="uq_privacy_export_artifacts_request_id"),
        )
    indexes = {item["name"] for item in inspect(bind).get_indexes("privacy_export_artifacts")}
    for name, column in (
        ("ix_privacy_export_artifacts_request_id", "request_id"),
        ("ix_privacy_export_artifacts_person_id", "person_id"),
        ("ix_privacy_export_artifacts_caregiver_id", "caregiver_id"),
        ("ix_privacy_export_artifacts_expires_at", "expires_at"),
    ):
        if name not in indexes:
            op.create_index(name, "privacy_export_artifacts", [column], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    if inspect(bind).has_table("privacy_export_artifacts"):
        op.drop_table("privacy_export_artifacts")
