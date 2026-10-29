"""Separate evidence-kind classification from epistemic status.

Revision ID: 20261029_04_evidence_kind
Revises: 20261029_03_evidence_provenance
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261029_04_evidence_kind"
down_revision = "20261029_03_evidence_provenance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("evidence"):
        raise RuntimeError("The evidence table must exist before classifications are added")
    existing = {column["name"] for column in inspector.get_columns("evidence")}
    if "evidence_kind" not in existing:
        op.add_column("evidence", sa.Column("evidence_kind", sa.String(length=40), nullable=True))
    if "epistemic_status" not in existing:
        op.add_column("evidence", sa.Column("epistemic_status", sa.String(length=32), nullable=True))


def downgrade() -> None:
    raise RuntimeError(
        "Refusing to remove evidence classification history; preserve kind and epistemic status."
    )
