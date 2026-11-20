"""Add S3-compatible storage references for original document binaries.

Revision ID: 20261120_02_document_object_storage
Revises: 20261120_01_reminder_assignment_version
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261120_02_document_object_storage"
down_revision = "20261120_01_reminder_assignment_version"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("evidence_uploads"):
        raise RuntimeError("evidence_uploads must exist before enabling object storage")

    columns = {column["name"]: column for column in inspect(bind).get_columns("evidence_uploads")}
    if "content" not in columns:
        raise RuntimeError("evidence_uploads.content is missing; refusing an unsafe object-storage migration")
    if "storage_backend" not in columns:
        op.add_column(
            "evidence_uploads",
            sa.Column("storage_backend", sa.String(length=16), nullable=False, server_default="postgres"),
        )
    if "storage_key" not in columns:
        op.add_column("evidence_uploads", sa.Column("storage_key", sa.String(length=512), nullable=True))

    # Existing rows keep their bytes in PostgreSQL and are explicitly marked as
    # such. Only new rows use S3 after the bucket variables are provisioned.
    bind.execute(
        sa.text("UPDATE evidence_uploads SET storage_backend = 'postgres' WHERE storage_backend IS NULL")
    )
    if not columns["content"]["nullable"]:
        op.alter_column(
            "evidence_uploads",
            "content",
            existing_type=sa.LargeBinary(),
            nullable=True,
        )

    constraints = {
        item.get("name") for item in inspect(bind).get_unique_constraints("evidence_uploads")
    }
    indexes = {item.get("name") for item in inspect(bind).get_indexes("evidence_uploads")}
    if "uq_evidence_uploads_storage_key" not in constraints | indexes:
        op.create_unique_constraint(
            "uq_evidence_uploads_storage_key",
            "evidence_uploads",
            ["storage_key"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("evidence_uploads"):
        return

    columns = {column["name"] for column in inspect(bind).get_columns("evidence_uploads")}
    if "storage_backend" in columns:
        external_count = bind.execute(
            sa.text("SELECT COUNT(*) FROM evidence_uploads WHERE storage_backend = 's3'")
        ).scalar_one()
        if external_count:
            raise RuntimeError(
                "Refusing to downgrade while S3-backed document binaries still exist. "
                "Migrate every binary back to PostgreSQL before downgrading."
            )

    constraints = {
        item.get("name") for item in inspect(bind).get_unique_constraints("evidence_uploads")
    }
    if "uq_evidence_uploads_storage_key" in constraints:
        op.drop_constraint("uq_evidence_uploads_storage_key", "evidence_uploads", type_="unique")

    columns = {column["name"] for column in inspect(bind).get_columns("evidence_uploads")}
    if "storage_key" in columns:
        op.drop_column("evidence_uploads", "storage_key")
    if "storage_backend" in columns:
        op.drop_column("evidence_uploads", "storage_backend")
    if "content" in columns:
        op.alter_column(
            "evidence_uploads",
            "content",
            existing_type=sa.LargeBinary(),
            nullable=False,
        )
