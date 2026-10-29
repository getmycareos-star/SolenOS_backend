"""Align conflict resolution evidence storage with the JSONList ORM type.

Revision ID: 20261029_02_conflict_evidence_text
Revises: 20261029_01_active_pattern_unique
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261029_02_conflict_evidence_text"
down_revision = "20261029_01_active_pattern_unique"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("contradiction_resolution_events"):
        raise RuntimeError("Conflict resolution history table is required")
    column = next(
        (item for item in inspector.get_columns("contradiction_resolution_events") if item["name"] == "evidence_ids"),
        None,
    )
    if column is None:
        raise RuntimeError("Conflict resolution evidence_ids column is missing")
    if "json" not in str(column["type"]).lower():
        return
    if bind.dialect.name == "postgresql":
        op.alter_column(
            "contradiction_resolution_events",
            "evidence_ids",
            existing_type=sa.JSON(),
            type_=sa.Text(),
            existing_nullable=False,
            postgresql_using="evidence_ids::text",
        )
    elif bind.dialect.name == "sqlite":
        with op.batch_alter_table("contradiction_resolution_events") as batch:
            batch.alter_column(
                "evidence_ids",
                existing_type=sa.JSON(),
                type_=sa.Text(),
                existing_nullable=False,
            )


def downgrade() -> None:
    bind = op.get_bind()
    if inspect(bind).has_table("contradiction_resolution_events"):
        raise RuntimeError("Refusing to downgrade conflict evidence storage; preserve compatible JSONList text encoding.")
