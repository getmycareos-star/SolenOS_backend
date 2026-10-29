"""Persist idempotency keys for evidence-ingestion retries.

Revision ID: 20261029_19_evidence_idem
Revises: 20261029_18_acq_provenance
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261029_19_evidence_idem"
down_revision = "20261029_18_acq_provenance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("evidence"):
        raise RuntimeError("The evidence table must exist before ingestion idempotency is added.")
    columns = {column["name"] for column in inspector.get_columns("evidence")}
    if "ingestion_key" not in columns:
        op.add_column("evidence", sa.Column("ingestion_key", sa.String(length=128), nullable=True))
    if "ingestion_request_sha256" not in columns:
        op.add_column("evidence", sa.Column("ingestion_request_sha256", sa.String(length=64), nullable=True))

    inspector = inspect(bind)
    unique_constraints = inspector.get_unique_constraints("evidence")
    indexes = inspector.get_indexes("evidence")
    exists = any(
        set(item.get("column_names") or []) == {"person_id", "ingestion_key"}
        for item in unique_constraints + indexes
        if item.get("unique", True)
    )
    if not exists:
        if bind.dialect.name == "sqlite":
            # A unique index avoids SQLite's table-rebuild batch mode, which
            # could otherwise remove previously installed evidence immutability triggers.
            op.create_index(
                "uq_evidence_person_ingestion_key",
                "evidence",
                ["person_id", "ingestion_key"],
                unique=True,
            )
        else:
            op.create_unique_constraint(
                "uq_evidence_person_ingestion_key", "evidence", ["person_id", "ingestion_key"]
            )


def downgrade() -> None:
    raise RuntimeError(
        "Refusing to discard evidence ingestion idempotency history during downgrade."
    )
