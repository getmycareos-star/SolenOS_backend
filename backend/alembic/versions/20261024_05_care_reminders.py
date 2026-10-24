"""Add persistent in-app care reminders.

Revision ID: 20261024_05
Revises: 20261023_04
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261024_05"
down_revision = "20261023_04"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("care_reminders"):
        op.create_table(
            "care_reminders",
            sa.Column("id", sa.String(), primary_key=True, nullable=False),
            sa.Column("person_id", sa.String(), sa.ForeignKey("persons.id"), nullable=False),
            sa.Column("created_by_caregiver_id", sa.String(), sa.ForeignKey("caregivers.id"), nullable=False),
            sa.Column("title", sa.String(length=180), nullable=False),
            sa.Column("note", sa.Text(), nullable=True),
            sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="open"),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("completed_by_caregiver_id", sa.String(), sa.ForeignKey("caregivers.id"), nullable=True),
            sa.Column("completion_note", sa.Text(), nullable=True),
            sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("cancelled_by_caregiver_id", sa.String(), sa.ForeignKey("caregivers.id"), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_care_reminders_person_id", "care_reminders", ["person_id"])
        op.create_index("ix_care_reminders_due_at", "care_reminders", ["due_at"])
        op.create_index("ix_care_reminders_status", "care_reminders", ["status"])


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if inspector.has_table("care_reminders"):
        indexes = {item["name"] for item in inspector.get_indexes("care_reminders")}
        for name in ("ix_care_reminders_status", "ix_care_reminders_due_at", "ix_care_reminders_person_id"):
            if name in indexes:
                op.drop_index(name, table_name="care_reminders")
        op.drop_table("care_reminders")
