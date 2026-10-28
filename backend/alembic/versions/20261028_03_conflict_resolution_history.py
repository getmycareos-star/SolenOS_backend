"""Persist immutable contradiction resolution and reopening history.

Revision ID: 20261028_03_conflict_hist
Revises: 20261028_02_change_confirmation
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261028_03_conflict_hist"
down_revision = "20261028_02_change_confirmation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("contradictions"):
        raise RuntimeError("Cannot add conflict history: contradictions table is required")
    if not inspector.has_table("contradiction_resolution_events"):
        op.create_table(
            "contradiction_resolution_events",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("contradiction_id", sa.String(), sa.ForeignKey("contradictions.id", ondelete="CASCADE"), nullable=False),
            sa.Column("person_id", sa.String(), sa.ForeignKey("persons.id", ondelete="CASCADE"), nullable=False),
            sa.Column("actor_caregiver_id", sa.String(), sa.ForeignKey("caregivers.id"), nullable=True),
            sa.Column("previous_status", sa.String(length=32), nullable=False),
            sa.Column("new_status", sa.String(length=32), nullable=False),
            sa.Column("rationale", sa.Text(), nullable=False),
            sa.Column("evidence_ids", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
    for table, column, name in (
        ("contradiction_resolution_events", "contradiction_id", "ix_contradiction_resolution_events_conflict"),
        ("contradiction_resolution_events", "person_id", "ix_contradiction_resolution_events_person"),
    ):
        if not any(index.get("name") == name for index in inspect(bind).get_indexes(table)):
            op.create_index(name, table, [column], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if inspector.has_table("contradiction_resolution_events"):
        if bind.execute(sa.text("SELECT 1 FROM contradiction_resolution_events LIMIT 1")).first():
            raise RuntimeError("Cannot downgrade while conflict resolution audit history exists.")
        op.drop_table("contradiction_resolution_events")
