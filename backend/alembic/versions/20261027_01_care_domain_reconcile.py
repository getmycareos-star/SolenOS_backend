"""Reconcile missing care-domain ORM tables on Railway without duplicating tables.

Revision ID: 20261027_01_care_domain_reconcile
Revises: 20261026_03_identity_security

Creates only tables absent from the current database, from the registered ORM
metadata. Existing rows and existing table definitions are not rewritten.
The evidence_uploads table remains the single original-file persistence model:
its evidence_id is the stable link to evidence, and content stores the original
bytes while sha256 and size_bytes support integrity verification.
"""
from alembic import op
from sqlalchemy import inspect

from app.core.database import Base

revision = "20261027_01_care_domain_reconcile"
down_revision = "20261026_03_identity_security"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()

    # Base imports/registers the complete current ORM model set. create_all with
    # checkfirst creates only absent tables; it does not drop or rewrite existing
    # tables or their data.
    Base.metadata.create_all(bind=bind, checkfirst=True)

    inspector = inspect(bind)
    required_tables = (
        "persons",
        "caregivers",
        "evidence",
        "evidence_uploads",
        "care_events",
        "care_transitions",
        "care_windows",
        "actions",
        "caregiver_activities",
        "caregiver_handoffs",
        "caregiver_communications",
        "share_intakes",
    )
    missing = [name for name in required_tables if not inspector.has_table(name)]
    if missing:
        raise RuntimeError(
            "Care-domain schema reconciliation incomplete; missing tables: "
            + ", ".join(missing)
        )

    # Do not silently accept a metadata-only document upload. Original bytes,
    # checksum, byte size and evidence association are mandatory.
    upload_columns = {
        column["name"] for column in inspector.get_columns("evidence_uploads")
    }
    required_upload_columns = {
        "evidence_id",
        "filename",
        "mime_type",
        "content",
        "size_bytes",
        "sha256",
        "created_at",
    }
    missing_upload_columns = sorted(required_upload_columns - upload_columns)
    if missing_upload_columns:
        raise RuntimeError(
            "evidence_uploads lacks required original-file integrity columns: "
            + ", ".join(missing_upload_columns)
            + ". Refusing to mark this migration complete."
        )

    # Ensure indexes declared by ORM metadata exist, including on tables that
    # predated this migration. checkfirst avoids duplicate index creation.
    existing_tables = set(inspector.get_table_names())
    for table in Base.metadata.sorted_tables:
        if table.name not in existing_tables:
            continue
        for index in table.indexes:
            index.create(bind=bind, checkfirst=True)


def downgrade():
    # Intentionally non-destructive. This reconciliation may have discovered
    # tables that contain production data; dropping them during rollback is unsafe.
    pass
