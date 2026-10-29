"""Persist distinct evidence provenance identities and source-text integrity.

Revision ID: 20261029_03_evidence_provenance
Revises: 20261029_02_conflict_text
"""
from __future__ import annotations

import hashlib

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect, select, update

revision = "20261029_03_evidence_provenance"
down_revision = "20261029_02_conflict_text"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("evidence"):
        raise RuntimeError("The evidence table must exist before provenance is extended")

    existing = {column["name"] for column in inspector.get_columns("evidence")}
    columns = [
        sa.Column("original_author", sa.Text(), nullable=True),
        sa.Column("reported_by_label", sa.Text(), nullable=True),
        sa.Column("reported_by_caregiver_id", sa.String(), nullable=True),
        sa.Column("collected_by_caregiver_id", sa.String(), nullable=True),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("occurred_at_timezone", sa.String(), nullable=True),
        sa.Column("origin_channel", sa.String(length=80), nullable=True),
        sa.Column("original_source_reference", sa.Text(), nullable=True),
        sa.Column("parent_evidence_id", sa.String(), nullable=True),
        sa.Column("source_text_sha256", sa.String(length=64), nullable=True),
        sa.Column("ingested_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    ]
    for column in columns:
        if column.name not in existing:
            op.add_column("evidence", column)

    # Add foreign keys after columns exist, and only once. Self-parent is nullable
    # and uses SET NULL so deleting a parent does not delete derived evidence.
    inspector = inspect(bind)
    foreign_keys = inspector.get_foreign_keys("evidence")
    fk_names = {fk.get("name") for fk in foreign_keys}
    constraints = [
        ("fk_evidence_reported_by_caregiver_id", "reported_by_caregiver_id", "caregivers", "id", None),
        ("fk_evidence_collected_by_caregiver_id", "collected_by_caregiver_id", "caregivers", "id", None),
        ("fk_evidence_parent_evidence_id", "parent_evidence_id", "evidence", "id", "SET NULL"),
    ]
    for name, column, target, target_column, ondelete in constraints:
        if column not in existing and column not in {c["name"] for c in inspector.get_columns("evidence")}:
            continue
        if any(fk.get("constrained_columns") == [column] for fk in foreign_keys):
            continue
        op.create_foreign_key(
            name,
            "evidence",
            target,
            [column],
            [target_column],
            ondelete=ondelete,
        )

    # Backfill a deterministic hash for every existing text source. This hash
    # covers the stored UTF-8 text only; it is not a claim about source authenticity
    # or about the bytes of an original uploaded file.
    evidence = sa.table(
        "evidence",
        sa.column("id", sa.String()),
        sa.column("source_text", sa.Text()),
        sa.column("source_text_sha256", sa.String(64)),
    )
    rows = bind.execute(
        select(evidence.c.id, evidence.c.source_text).where(
            evidence.c.source_text_sha256.is_(None)
        )
    ).mappings()
    for row in rows:
        source_text = row["source_text"]
        if source_text is None:
            continue
        digest = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
        bind.execute(
            update(evidence)
            .where(evidence.c.id == row["id"])
            .values(source_text_sha256=digest)
        )


def downgrade() -> None:
    raise RuntimeError(
        "Refusing to remove evidence provenance and source integrity history; "
        "preserve the added metadata and hashes."
    )
