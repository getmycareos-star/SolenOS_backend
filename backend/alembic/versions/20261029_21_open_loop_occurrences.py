"""Persist individual occurrences linked to unresolved open loops.

Revision ID: 20261029_21_open_loop_occurrences
Revises: 20261029_20_open_loops
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261029_21_open_loop_occurrences"
down_revision = "20261029_20_open_loops"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("open_loop_occurrences"):
        op.create_table(
            "open_loop_occurrences",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("loop_id", sa.String(), sa.ForeignKey("open_loop_records.id", ondelete="CASCADE"), nullable=False),
            sa.Column("person_id", sa.String(), sa.ForeignKey("persons.id"), nullable=False),
            sa.Column("caregiver_id", sa.String(), sa.ForeignKey("caregivers.id"), nullable=False),
            sa.Column("source_type", sa.String(80), nullable=True),
            sa.Column("source_id", sa.String(160), nullable=True),
            sa.Column("source_fingerprint", sa.String(64), nullable=False),
            sa.Column("evidence", sa.JSON(), nullable=False),
            sa.Column("idempotency_key", sa.String(160), nullable=True),
            sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("person_id", "idempotency_key", name="uq_open_loop_occurrence_idempotency"),
            sa.UniqueConstraint("person_id", "source_type", "source_id", "loop_id", name="uq_open_loop_occurrence_source"),
        )
    inspector = inspect(bind)
    existing_indexes = {item["name"] for item in inspector.get_indexes("open_loop_occurrences")}
    for name, columns in (
        ("ix_open_loop_occurrences_loop_id", ["loop_id"]),
        ("ix_open_loop_occurrences_person_id", ["person_id"]),
        ("ix_open_loop_occurrences_caregiver_id", ["caregiver_id"]),
        ("ix_open_loop_occurrence_record_created", ["loop_id", "created_at"]),
    ):
        if name not in existing_indexes:
            op.create_index(name, "open_loop_occurrences", columns)


def downgrade() -> None:
    raise RuntimeError(
        "Refusing to discard persisted open-loop occurrence history during downgrade."
    )
