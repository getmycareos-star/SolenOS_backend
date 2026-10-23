"""Add deadlines, recipient access scope, and completion reporting to handoffs.

Revision ID: t5_handoff_accountability_20261023
Revises: t3_care_handoffs_20261023
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "t5_handoff_accountability_20261023"
down_revision = "t3_care_handoffs_20261023"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("care_handoffs"):
        return
    existing = {column["name"] for column in inspector.get_columns("care_handoffs")}
    additions = [
        ("access_scope", sa.String(), False, "care_circle"),
        ("due_at", sa.DateTime(timezone=True), True, None),
        ("reminder_at", sa.DateTime(timezone=True), True, None),
        ("completion_report", sa.Text(), True, None),
        ("completed_by_caregiver_id", sa.String(), True, None),
        ("completed_at", sa.DateTime(timezone=True), True, None),
    ]
    for name, kind, nullable, default in additions:
        if name not in existing:
            kwargs = {"nullable": nullable}
            if default is not None:
                kwargs["server_default"] = default
            if name == "completed_by_caregiver_id":
                op.add_column("care_handoffs", sa.Column(name, kind, sa.ForeignKey("caregivers.id"), **kwargs))
            else:
                op.add_column("care_handoffs", sa.Column(name, kind, **kwargs))
    indexes = {item["name"] for item in inspect(bind).get_indexes("care_handoffs")}
    if "ix_care_handoffs_due_at" not in indexes:
        op.create_index("ix_care_handoffs_due_at", "care_handoffs", ["person_id", "due_at"], unique=False)


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("care_handoffs"):
        return
    indexes = {item["name"] for item in inspect(bind).get_indexes("care_handoffs")}
    if "ix_care_handoffs_due_at" in indexes:
        op.drop_index("ix_care_handoffs_due_at", table_name="care_handoffs")
    existing = {column["name"] for column in inspect(bind).get_columns("care_handoffs")}
    for name in ("completed_at", "completed_by_caregiver_id", "completion_report", "reminder_at", "due_at", "access_scope"):
        if name in existing:
            op.drop_column("care_handoffs", name)
