"""Persist change confirmation state and immutable transition history.

Revision ID: 20261028_02_change_confirmation
Revises: 20261028_01_profile_link
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261028_02_change_confirmation"
down_revision = "20261028_01_profile_link"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("changes"):
        raise RuntimeError("Cannot add change confirmation state: changes table is required")
    columns = {column["name"] for column in inspector.get_columns("changes")}
    if "confirmation_status" not in columns:
        op.add_column(
            "changes",
            sa.Column("confirmation_status", sa.String(length=32), nullable=False, server_default="detected"),
        )
    inspector = inspect(bind)
    if not inspector.has_table("change_confirmation_events"):
        op.create_table(
            "change_confirmation_events",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("change_id", sa.String(), sa.ForeignKey("changes.id", ondelete="CASCADE"), nullable=False),
            sa.Column("person_id", sa.String(), sa.ForeignKey("persons.id", ondelete="CASCADE"), nullable=False),
            sa.Column("actor_caregiver_id", sa.String(), sa.ForeignKey("caregivers.id"), nullable=False),
            sa.Column("previous_status", sa.String(length=32), nullable=False),
            sa.Column("new_status", sa.String(length=32), nullable=False),
            sa.Column("reason", sa.Text(), nullable=True),
            sa.Column("evidence_ids", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
    inspector = inspect(bind)
    for table, column, name in (
        ("changes", "confirmation_status", "ix_changes_confirmation_status"),
        ("change_confirmation_events", "change_id", "ix_change_confirmation_events_change_id"),
        ("change_confirmation_events", "person_id", "ix_change_confirmation_events_person_id"),
        ("change_confirmation_events", "actor_caregiver_id", "ix_change_confirmation_events_actor_caregiver_id"),
    ):
        if not any(
            index.get("name") == name
            for index in inspect(bind).get_indexes(table)
        ):
            op.create_index(name, table, [column], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if inspector.has_table("change_confirmation_events"):
        op.drop_table("change_confirmation_events")
    if inspector.has_table("changes"):
        columns = {column["name"] for column in inspector.get_columns("changes")}
        if "confirmation_status" in columns:
            for index in inspect(bind).get_indexes("changes"):
                if index.get("name") == "ix_changes_confirmation_status":
                    op.drop_index(index["name"], table_name="changes")
            op.drop_column("changes", "confirmation_status")
